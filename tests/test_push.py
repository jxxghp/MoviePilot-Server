"""
私有推送的真实路由、临时数据库、Redis 能力码与外部投递边界回归。
"""

import asyncio
import json
from pathlib import Path

import fakeredis.aioredis
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.redis as redis_module
from app.api.push import get_delivery, router
from app.core.config import Settings, settings
from app.db.deps import get_db
from app.models import Base, PushDevice
from app.schemas.push import PushMessage
from app.services.push.delivery import PushDelivery

KEY_A, KEY_B = "a" * 40, "b" * 40
OWNER_A, OWNER_B = "a" * 32, "b" * 32


class DeliveryDouble:
    """
    记录实际选中的设备归属并按指定结果回应，不访问外部服务。
    """

    def __init__(self):
        """
        初始化调用证据和可切换的设备失效响应。
        """
        self.calls = []
        self.gone = False

    def apns_available(self):
        """
        测试实例提供可注册的 APNs 通道。
        """
        return True

    def public_key(self):
        """
        仅让配对接口通过配置检查，不参与浏览器加密。
        """
        return "public-test-key"

    async def send(self, device, message):
        """
        保存设备与用户关系，模拟服务接受或设备失效。
        """
        self.calls.append((device.namespace, device.owner, device.destination, message))
        return not self.gone, self.gone, "已接受" if not self.gone else "设备失效"


def run_scenario(monkeypatch, tmp_path, scenario):
    """
    每项场景使用独立 SQLite 与 fakeredis，依赖替换随用例恢复。
    """
    async def run():
        """
        安装隔离路由并在结束时关闭数据库与 Redis。
        """
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(redis_module, "redis_client", fake)
        monkeypatch.setattr(settings, "PUSH_RELAY_KEYS", KEY_A + "," + KEY_B)
        monkeypatch.setattr(settings, "PUSH_APNS_TOPICS", "com.example.moviepilot")
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'push.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def database():
            """
            每个请求独占会话，模拟正式服务的请求事务边界。
            """
            async with factory() as db:
                yield db

        app = FastAPI()
        app.include_router(router, prefix="/push")
        delivery = DeliveryDouble()
        app.dependency_overrides[get_delivery] = lambda: delivery
        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
                await scenario(client, factory, delivery, fake)
        finally:
            await engine.dispose()
            await fake.aclose()

    asyncio.run(run())


def auth(key=KEY_A):
    """
    构造仅在请求头传输的测试中转凭据。
    """
    return {"Authorization": "Bearer " + key}


def registration(owner=OWNER_A, installation="installation-0001", token="c" * 64):
    """
    构造系统令牌注册请求，身份来自后端而非网页配对请求。
    """
    return {"owner": owner, "origin": "https://moviepilot.test/mp", "installation": installation,
            "token": token, "topic": "com.example.moviepilot", "environment": "sandbox"}


def test_concurrent_installations_share_owner_device_quota(monkeypatch, tmp_path):
    """
    不同安装同时注册时仍共用账号配额，令牌轮换不会额外占用配额。
    """
    async def scenario(client, factory, delivery, redis):
        """
        先占十九个位置，再并发竞争最后一个位置并验证持久数量。
        """
        for index in range(19):
            body = registration(installation=f"installation-{index:04d}", token=f"{index:064x}")
            assert (await client.post('/push/devices', json=body, headers=auth())).status_code == 200
        results = await asyncio.gather(*(
            client.post('/push/devices', json=registration(installation=f"installation-{index:04d}", token=f"{index:064x}"), headers=auth())
            for index in (19, 20)
        ))
        assert sorted(response.status_code for response in results) == [200, 409]
        body = registration(installation="installation-0000", token="f" * 64)
        assert (await client.post('/push/devices', json=body, headers=auth())).status_code == 200
        async with factory() as db:
            assert len((await db.execute(select(PushDevice))).scalars().all()) == 20

    run_scenario(monkeypatch, tmp_path, scenario)


def test_restored_web_capability_cannot_bypass_disabled_instance(monkeypatch, tmp_path):
    """
    通过真实订阅恢复的能力遵守实例停用和解绑，不形成另一条绕过通道。
    """
    from app.models.push import PushRelay
    from app.services.push.registry import create_pairing

    async def scenario(client, factory, _delivery, _redis):
        """
        在临时自动接入空间绑定、恢复、停用，校验所有只读端点同时拒绝。
        """
        namespace = 'e' * 32
        async with factory() as db:
            db.add(PushRelay(id=namespace, user_uid='test-user-identity', enabled=True, created_at=1, updated_at=1))
            await db.commit()
        code = await create_pairing(namespace, OWNER_A, 'https://moviepilot.test', 'parent-installation-01')
        subscription = {'endpoint': 'https://web.push.apple.com/endpoint/restore', 'keys': {'p256dh': 'c' * 87, 'auth': 'd' * 22}}
        bound = (await client.post('/push/subscriptions', json={
            'code': code, 'installation': 'web-installation-01', 'subscription': subscription,
        })).json()
        response = await client.post('/push/subscriptions/restore', json={'subscription': subscription})
        assert response.status_code == 200
        recovered = response.json()
        async with factory() as db:
            relay = await db.get(PushRelay, namespace)
            relay.enabled = False
            await db.commit()
        capability = {'id': recovered['id'], 'secret': recovered['secret']}
        assert (await client.post('/push/subscriptions/restore', json={'subscription': subscription})).status_code == 403
        assert (await client.post('/push/subscriptions/latest', json=capability)).status_code == 403
        assert (await client.post('/push/subscriptions/message', json={**capability, 'message': 'f' * 32})).status_code == 403
        assert (await client.request('DELETE', '/push/subscriptions', json=capability)).status_code == 200
        assert (await client.post('/push/subscriptions/restore', json={'subscription': subscription})).status_code == 404
        assert (await client.post('/push/subscriptions/test', json={'id': bound['id'], 'secret': bound['secret']})).status_code == 404

    run_scenario(monkeypatch, tmp_path, scenario)


def test_private_relay_auth_user_isolation_rotation_and_logout(monkeypatch, tmp_path):
    """
    无凭据拒绝；发送、令牌轮换、跨用户注销均使用精确命名空间。
    """
    async def scenario(client, factory, delivery, _redis):
        """
        通过真实 HTTP 路由构造两实例、两用户与同一安装的身份切换。
        """
        assert (await client.post('/push/devices', json=registration())).status_code == 401
        assert (await client.post('/push/devices', json=registration(), headers=auth('wrong'))).status_code == 401
        assert (await client.post('/push/devices', json=registration(), headers=auth())).status_code == 200
        assert (await client.post('/push/devices', json=registration(OWNER_B, 'installation-0002', 'd' * 64), headers=auth())).status_code == 200
        assert (await client.post('/push/devices', json=registration(OWNER_A, 'installation-0003', 'e' * 64), headers=auth(KEY_B))).status_code == 200
        message = {"owner": OWNER_A, "title": "整理完成", "body": "中文通知", "route": "/history"}
        result = (await client.post('/push/messages', json=message, headers=auth())).json()
        assert result == {"total": 1, "accepted": 1, "failed": []}
        assert [call[2] for call in delivery.calls] == ['c' * 64]
        # 知道安装 ID 也不能以其他用户的身份注销。
        await client.request('DELETE', '/push/devices', json={"owner": OWNER_B, "installation": "installation-0001"}, headers=auth())
        assert len((await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth())).json()['devices']) == 1
        # 同一安装重新登录时旧用户不再有此设备，新令牌持久化且无重复。
        await client.post('/push/devices', json=registration(OWNER_B, token='f' * 64), headers=auth())
        assert (await client.post('/push/messages', json=message, headers=auth())).json()['total'] == 0
        async with factory() as db:
            rows = (await db.execute(select(PushDevice))).scalars().all()
        assert len(rows) == 3
        assert all(row.namespace not in {KEY_A, KEY_B} for row in rows)
        await client.request('DELETE', '/push/devices', json={"owner": OWNER_B, "installation": "installation-0001"}, headers=auth())
        await client.post('/push/messages', json={**message, "owner": OWNER_B}, headers=auth())
        assert delivery.calls[-1][2] == 'd' * 64

    run_scenario(monkeypatch, tmp_path, scenario)


def test_pairing_is_single_use_and_web_management_is_device_scoped(monkeypatch, tmp_path):
    """
    网页只能用一次性码绑定；端点 SSRF、重放与越权管理均被拒绝。
    """
    async def scenario(client, factory, delivery, redis):
        """
        注册真正的浏览器订阅结构并验证持久化、脱敏和失效清理。
        """
        pair = (await client.post('/push/pairings', json={"owner": OWNER_A, "origin": "https://moviepilot.test", "installation": "app-installation-01"}, headers=auth())).json()
        assert pair['expires_in'] == 600
        assert all(pair['code'] not in key for key in await redis.keys('*'))
        web = {"code": pair['code'], "installation": "browser-installation-01", "subscription": {
            "endpoint": "https://web.push.apple.com/device/example", "keys": {"p256dh": "c" * 87, "auth": "d" * 22}, "expirationTime": None,
        }}
        invalid = {**web, "subscription": {**web['subscription'], "endpoint": "https://127.0.0.1/private"}}
        assert (await client.post('/push/subscriptions', json=invalid)).status_code == 422
        response = await client.post('/push/subscriptions', json=web)
        assert response.status_code == 200
        binding = response.json()
        assert (await client.post('/push/subscriptions', json=web)).status_code == 400
        management = {"id": binding['id'], "secret": binding['secret']}
        assert (await client.post('/push/subscriptions/test', json={**management, "secret": "bad" * 16})).status_code == 404
        assert (await client.post('/push/subscriptions/test', json=management)).json()['accepted'] is True
        listing = (await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth())).text
        assert binding['secret'] not in listing and 'web.push.apple.com' not in listing
        async with factory() as db:
            device = await db.get(PushDevice, binding['id'])
            assert device.secret_hash != binding['secret']
        delivery.gone = True
        assert (await client.post('/push/subscriptions/test', json=management)).json()['accepted'] is False
        assert (await client.post('/push/subscriptions/test', json=management)).status_code == 404

    run_scenario(monkeypatch, tmp_path, scenario)


def test_expired_codes_foreign_routes_topic_and_abuse_limit(monkeypatch, tmp_path):
    """
    失效绑定码、其他 topic、外站跳转和超过共享配额的请求均不可接受。
    """
    async def scenario(client, _factory, _delivery, redis):
        """
        错误输入不会进入投递器，跨请求配额保存在共享 Redis。
        """
        assert (await client.post('/push/devices', json={**registration(), "topic": "other.app"}, headers=auth())).status_code == 409
        assert (await client.post('/push/messages', json={"owner": OWNER_A, "route": "//foreign.test"}, headers=auth())).status_code == 422
        for _index in range(60):
            last = await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth())
        assert last.status_code == 429
        assert last.headers['Retry-After'] == '60'

    run_scenario(monkeypatch, tmp_path, scenario)


def test_apns_wire_signature_environment_payload_and_invalid_token(tmp_path):
    """
    用 HTTP MockTransport 验证真实 ES256 签名与 Apple 请求格式，绝不实际出站。
    """
    private = ec.generate_private_key(ec.SECP256R1())
    key_file = tmp_path / 'AuthKey.p8'
    key_file.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    config = Settings(PUSH_APNS_KEY_FILE=str(key_file), PUSH_APNS_KEY_ID='KEY1234567',
                      PUSH_APNS_TEAM_ID='TEAM123456', PUSH_APNS_TOPICS='com.example.moviepilot')
    observed = []

    def handler(request):
        """
        在传输边界校验 JWT、topic、环境、中文载荷及令牌失效语义。
        """
        observed.append(request)
        claims = jwt.decode(request.headers['authorization'].split()[1], private.public_key(), algorithms=['ES256'])
        assert claims['iss'] == 'TEAM123456'
        assert request.url.host == 'api.sandbox.push.apple.com'
        assert request.headers['apns-topic'] == 'com.example.moviepilot'
        assert request.headers['apns-push-type'] == 'alert'
        assert json.loads(request.content)['moviepilot']['owner'] == OWNER_A
        return httpx.Response(410, json={"reason": "Unregistered"})

    async def scenario():
        """
        同一投递器复用连接和 provider token，过大载荷在出站前拒绝。
        """
        delivery = PushDelivery(config, transport=httpx.MockTransport(handler))
        device = PushDevice(transport='apns', destination='c' * 64, owner=OWNER_A,
                            topic='com.example.moviepilot', environment='sandbox', origin='https://moviepilot.test')
        try:
            accepted, gone, reason = await delivery.send(device, PushMessage(owner=OWNER_A, title='测试', body='中文通知'))
            assert not accepted and gone and '410' in reason
            accepted, gone, reason = await delivery.send(device, PushMessage(owner=OWNER_A, body='中' * 3000))
            assert not accepted and not gone and '4 KiB' in reason
            assert len(observed) == 1
        finally:
            await delivery.close()

    asyncio.run(scenario())


def test_logout_revokes_paired_web_devices_and_unconsumed_codes(monkeypatch, tmp_path):
    """
    App 安装注销同时撤销配对网页和未使用能力码，其他安装不受影响。
    """
    async def scenario(client, _factory, _delivery, _redis):
        """
        真实路由完成绑定、注销及重放，验证 Redis 锁与持久数据库共同收敛。
        """
        body = {"owner": OWNER_A, "origin": "https://moviepilot.test", "installation": "parent-installation-01"}
        used = (await client.post('/push/pairings', json=body, headers=auth())).json()
        pending = (await client.post('/push/pairings', json=body, headers=auth())).json()
        web = {"code": used['code'], "installation": "web-installation-01", "subscription": {
            "endpoint": "https://web.push.apple.com/endpoint/test", "keys": {"p256dh": "c" * 87, "auth": "d" * 22},
        }}
        binding = (await client.post('/push/subscriptions', json=web)).json()
        listing = await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth())
        assert listing.headers['Cache-Control'] == 'no-store'
        assert listing.json()['devices'][0]['parent_installation'] == body['installation']
        await client.request('DELETE', '/push/devices', json={"owner": OWNER_A, "installation": body['installation']}, headers=auth())
        assert (await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth())).json()['devices'] == []
        assert (await client.post('/push/subscriptions/test', json={"id": binding['id'], "secret": binding['secret']})).status_code == 404
        assert (await client.post('/push/subscriptions', json={**web, "code": pending['code']})).status_code == 400

    run_scenario(monkeypatch, tmp_path, scenario)


def test_old_delivery_failure_cannot_delete_rotated_token(monkeypatch, tmp_path):
    """
    旧请求收到 Gone 后，按版本清理不能删除同一行刚轮换的新令牌。
    """
    from app.services.push.registry import prune_revision

    async def scenario(client, factory, _delivery, _redis):
        """
        使用两个独立会话模拟在途发送与注册竞争。
        """
        await client.post('/push/devices', json=registration(), headers=auth())
        async with factory() as old_db:
            old = (await old_db.execute(select(PushDevice))).scalars().one()
            await client.post('/push/devices', json=registration(token='f' * 64), headers=auth())
            await prune_revision(old_db, old)
        async with factory() as db:
            assert (await db.execute(select(PushDevice))).scalars().one().destination == 'f' * 64

    run_scenario(monkeypatch, tmp_path, scenario)


@pytest.mark.parametrize('body', ['中文通知', '通知全文与海报\n' * 3000])
def test_webpush_encrypts_payload_and_signs_vapid_without_real_network(monkeypatch, tmp_path, body):
    """
    验证实际 pywebpush 加密与 VAPID 签名，网络边界替换后由浏览器私钥解密断言正文。
    """
    import base64
    import os
    import http_ece
    import requests

    vapid = ec.generate_private_key(ec.SECP256R1())
    browser = ec.generate_private_key(ec.SECP256R1())
    auth_secret = os.urandom(16)
    key_path = tmp_path / 'vapid.pem'
    key_path.write_bytes(vapid.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    def encoded(value):
        """
        按浏览器订阅格式序列化二进制公钥及认证盐。
        """
        return base64.urlsafe_b64encode(value).decode().rstrip('=')

    endpoint = 'https://web.push.apple.com/endpoint/test'
    device = PushDevice(transport='webpush', owner=OWNER_A, origin='https://moviepilot.test/base', destination=json.dumps({
        'endpoint': endpoint, 'keys': {'p256dh': encoded(browser.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)), 'auth': encoded(auth_secret)},
    }))
    observed = []

    def post(url, **kwargs):
        """
        校验真实发出密文及签名，不访问 Apple 或其他外部服务。
        """
        assert url == endpoint and kwargs['timeout'] == 10
        assert kwargs['headers']['content-encoding'] == 'aes128gcm'
        assert kwargs['headers']['ttl'] == '300'
        authorization = kwargs['headers']['Authorization']
        token = authorization.split('t=', 1)[1].split(',', 1)[0]
        claims = jwt.decode(token, vapid.public_key(), algorithms=['ES256'], audience='https://web.push.apple.com')
        assert claims['sub'] == 'mailto:admin@example.com'
        decrypted = http_ece.decrypt(kwargs['data'], private_key=browser, auth_secret=auth_secret, version='aes128gcm')
        observed.append(json.loads(decrypted))
        response = requests.Response()
        response.status_code = 201
        response._content = b''
        return response

    monkeypatch.setattr(requests, 'post', post)
    delivery = PushDelivery(Settings(PUSH_VAPID_KEY_FILE=str(key_path), PUSH_VAPID_SUBJECT='mailto:admin@example.com'))
    image = 'https://images.example.com/poster.jpg'
    assert delivery._send_web(device, PushMessage(owner=OWNER_A, title='下载完成', body=body, route='/history', image=image), 'c' * 32)[0]
    assert observed[0]['owner'] == OWNER_A
    assert observed[0]['url'] == 'https://moviepilot.test/base/#/history'
    assert observed[0]['body'] == body.encode()[:1024].decode('utf-8', errors='ignore')
    assert observed[0]['id'] == 'c' * 32
    assert len(json.dumps(observed[0], ensure_ascii=False, separators=(',', ':')).encode()) <= 3800
    assert observed[0]['image'] == image


@pytest.mark.parametrize('image', ['javascript:alert(1)', 'data:image/png;base64,AA', '/poster.jpg',
                                  'file:///private/poster.png', 'https://user:secret@images.test/poster.jpg',
                                  'https://images.test/\\poster.jpg', 'https://images.test/\nposter.jpg'])
def test_push_message_rejects_unsafe_images(image):
    """
    图片只允许无凭据网页地址，不将脚本、文件及畸形地址交给接收设备。
    """
    with pytest.raises(ValueError):
        PushMessage(owner=OWNER_A, image=image)
    assert PushMessage(owner=OWNER_A).image is None


def test_notification_page_allows_posters_without_allowing_remote_scripts(monkeypatch, tmp_path):
    """
    实际静态响应允许设备加载海报，脚本仍限定同源且不缓存通知内容。
    """
    async def scenario(client, _factory, _delivery, _redis):
        """
        校验公开详情页和 worker 的响应头，不连接真实图片服务器。
        """
        for asset in ('', 'app.js', 'worker.js', 'messages.js'):
            response = await client.get('/push/app/' + asset)
            assert response.status_code == 200
            policy = response.headers['content-security-policy']
            assert "default-src 'self';" in policy
            assert "img-src 'self' data: https: http:;" in policy
            assert "object-src 'none'" in policy
            assert response.headers['referrer-policy'] == 'no-referrer'
            assert response.headers['cache-control'] == 'no-cache'
    run_scenario(monkeypatch, tmp_path, scenario)


def test_full_web_message_is_device_scoped_bounded_and_revoked(monkeypatch, tmp_path):
    """
    实际投递暂存全文，路由验证设备隔离、配额淘汰、过期与换绑失效。
    """
    from app.services.push.messages import message_key

    wire = []
    monkeypatch.setattr('app.services.push.delivery.webpush', lambda **kwargs: wire.append(json.loads(kwargs['data'])))

    async def scenario(client, factory, _double, redis):
        """
        只替换系统推送网络边界，使用实际绑定、投递、Redis 脚本及全文读取端点。
        """
        async def bind(installation, owner=OWNER_A):
            """
            不同网页以各自一次性码建立独立能力，避免测试共享管理令牌。
            """
            pair = (await client.post('/push/pairings', headers=auth(), json={
                'owner': owner, 'origin': 'https://moviepilot.test/base', 'installation': 'parent-' + installation,
            })).json()
            result = await client.post('/push/subscriptions', json={
                'code': pair['code'], 'installation': installation, 'subscription': {
                    'endpoint': 'https://web.push.apple.com/endpoint/' + installation,
                    'keys': {'p256dh': installation[-1] * 87, 'auth': installation[-1] * 22},
                },
            })
            assert result.status_code == 200
            return result.json()

        first = await bind('web-installation-01')
        other = await bind('web-installation-02')
        outsider = await bind('web-installation-03', OWNER_B)
        async with factory() as db:
            device = await db.get(PushDevice, first['id'])
        delivery = PushDelivery(Settings(PUSH_VAPID_KEY_FILE='not-opened.pem', PUSH_VAPID_SUBJECT='mailto:test@example.com'))
        full = PushMessage(owner=OWNER_A, title='下载完成', body='全文保留，不截断。\n' * 6000,
                           image='https://images.test/poster.jpg', route='/history')
        assert (await delivery.send(device, full))[0]
        identifier = wire[-1]['id']
        capability = {'id': first['id'], 'secret': first['secret'], 'message': identifier}
        response = await client.post('/push/subscriptions/message', json=capability)
        assert response.status_code == 200
        assert response.json()['body'] == full.body
        assert response.json()['image'] == full.image
        assert response.json()['origin'] == device.origin
        assert response.headers['cache-control'] == 'no-store'
        assert 0 < await redis.ttl(message_key(device) + ':' + identifier) <= 86400
        subscription = {'endpoint': 'https://web.push.apple.com/endpoint/web-installation-01',
                        'keys': {'p256dh': '1' * 87, 'auth': '1' * 22}}
        recovered = await client.post('/push/subscriptions/restore', json={'subscription': subscription})
        assert recovered.status_code == 200
        restored = recovered.json()
        assert restored['id'] == first['id'] and restored['message']['body'] == full.body
        assert recovered.headers['cache-control'] == 'no-store'
        restored_capability = {'id': restored['id'], 'secret': restored['secret'], 'message': identifier}
        assert (await client.post('/push/subscriptions/message', json=restored_capability)).status_code == 200
        assert (await client.post('/push/subscriptions/message', json=capability)).status_code == 200
        assert (await client.post('/push/subscriptions/latest', json={'id': restored['id'], 'secret': restored['secret']})).json()['id'] == identifier
        for keys in ({'p256dh': '2' * 87, 'auth': '1' * 22}, {'p256dh': '1' * 87, 'auth': '2' * 22}):
            denied = await client.post('/push/subscriptions/restore', json={'subscription': {**subscription, 'keys': keys}})
            assert denied.status_code == 404
        for binding in (other, outsider, {**first, 'secret': 'wrong-secret-' * 4}):
            result = await client.post('/push/subscriptions/message', json={
                'id': binding['id'], 'secret': binding['secret'], 'message': identifier,
            })
            assert result.status_code == 404
        # 第二十一次投递原子淘汰第一条，既限制条数，也移除对应全文键。
        for _ in range(20):
            assert (await delivery.send(device, full))[0]
        assert await redis.llen(message_key(device)) == 20
        assert not await redis.exists(message_key(device) + ':' + identifier)
        assert (await client.post('/push/subscriptions/message', json=capability)).status_code == 404
        latest = wire[-1]['id']
        await redis.expire(message_key(device) + ':' + latest, 0)
        assert (await client.post('/push/subscriptions/message', json={**capability, 'message': latest})).status_code == 404
        assert (await delivery.send(device, full))[0]
        retained = wire[-1]['id']
        rebound = await bind('web-installation-01')
        assert (await client.post('/push/subscriptions/message', json={**capability, 'message': retained})).status_code == 404
        assert (await client.post('/push/subscriptions/message', json=restored_capability)).status_code == 404
        replacement = {'id': rebound['id'], 'secret': rebound['secret'], 'message': retained}
        assert (await client.post('/push/subscriptions/message', json=replacement)).status_code == 404
        await client.request('DELETE', '/push/subscriptions', json={'id': rebound['id'], 'secret': rebound['secret']})
        assert (await client.post('/push/subscriptions/message', json=replacement)).status_code == 404

    run_scenario(monkeypatch, tmp_path, scenario)
