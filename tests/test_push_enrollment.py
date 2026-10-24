"""
中心自动接入的真实路由、凭据生命周期、同 UID 隔离和运营权限回归。
"""

import asyncio
import json
import time

from sqlalchemy import func, select

from app.core.config import settings
from app.models.push import PushRelay, PushRelayKey
from app.services.push.registry import digest
from test_push import OWNER_A, auth, registration, run_scenario

UID = "deployment-user-unique-id-0001"
AUTO_A, AUTO_B, AUTO_C = ("mpa_" + value * 43 for value in ("A", "B", "C"))


def test_management_page_can_be_opened_directly_and_reloaded(monkeypatch, tmp_path):
    """
    运营者直接打开或刷新推送实例页时返回前端壳，未知管理 API 仍返回 404。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.admin.static import install_admin_pages

    (tmp_path / "index.html").write_text("<html>推送实例</html>")
    monkeypatch.setattr("app.admin.static.DIST", tmp_path)
    app = FastAPI()
    install_admin_pages(app)
    with TestClient(app) as client:
        response = client.get("/admin/push")
        assert response.status_code == 200 and "推送实例" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/admin/api/unknown").status_code == 404


def test_automatic_instances_with_identical_uid_cannot_share_devices(monkeypatch, tmp_path):
    """
    UID 相同也不合并权限空间，不能通过自报 UID 认领、查询或删除已有设备。
    """
    async def scenario(client, factory, delivery, _redis):
        """
        直接经过登记和消息接口验证服务端强制的实例范围。
        """
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", True)
        first = await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))
        second = await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_B))
        assert first.status_code == second.status_code == 200
        assert first.json()['instance'] != second.json()['instance']
        repeated = await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))
        assert repeated.json() == first.json()
        assert (await client.post('/push/devices', json=registration(), headers=auth(AUTO_A))).status_code == 200
        listed = await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth(AUTO_B))
        assert listed.json() == {"devices": []}
        message = {"owner": OWNER_A, "title": "消息"}
        assert (await client.post('/push/messages', json=message, headers=auth(AUTO_B))).json()['total'] == 0
        await client.request('DELETE', '/push/devices', json={"owner": OWNER_A, "installation": "installation-0001"}, headers=auth(AUTO_B))
        assert (await client.post('/push/messages', json=message, headers=auth(AUTO_A))).json()['accepted'] == 1
        assert delivery.calls[-1][0] == first.json()['instance']
        async with factory() as db:
            keys = (await db.scalars(select(PushRelayKey))).all()
            assert len(keys) == 2 and all(item.digest not in {AUTO_A, AUTO_B} for item in keys)
        assert AUTO_A not in first.text and digest(AUTO_A) not in first.text
        assert (await client.get('/push/instances', headers=auth(AUTO_A))).status_code == 405
        assert (await client.post('/push/instances', json={"user_uid": UID, "instance": first.json()['instance']}, headers=auth(AUTO_B))).status_code == 422
    run_scenario(monkeypatch, tmp_path, scenario)


def test_rotation_survives_lost_response_without_restoring_old_keys(monkeypatch, tmp_path):
    """
    轮换保持设备、立即拒绝旧密钥，完全相同的请求可重试但不能再次改写。
    """
    async def scenario(client, _factory, _delivery, _redis):
        """
        模拟轮换成功响应丢失、再次轮换及历史凭据重新登记攻击。
        """
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", True)
        instance = (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).json()['instance']
        await client.post('/push/devices', json=registration(), headers=auth(AUTO_A))
        rotated = await client.put('/push/instances/credentials', json={"key": AUTO_B}, headers=auth(AUTO_A))
        assert rotated.json() == {"instance": instance, "rotated": True}
        assert (await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth(AUTO_A))).status_code == 401
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).status_code == 401
        retried = await client.put('/push/instances/credentials', json={"key": AUTO_B}, headers=auth(AUTO_A))
        assert retried.status_code == 200 and retried.json() == rotated.json()
        assert len((await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth(AUTO_B))).json()['devices']) == 1
        assert (await client.put('/push/instances/credentials', json={"key": AUTO_C}, headers=auth(AUTO_A))).status_code == 401
        assert (await client.put('/push/instances/credentials', json={"key": AUTO_C}, headers=auth(AUTO_B))).status_code == 200
        assert (await client.put('/push/instances/credentials', json={"key": AUTO_B}, headers=auth(AUTO_A))).status_code == 401
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).status_code == 401
        assert (await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth(AUTO_C))).status_code == 200
    run_scenario(monkeypatch, tmp_path, scenario)


def test_registration_is_explicit_limited_and_does_not_disable_existing_instances(monkeypatch, tmp_path):
    """
    默认不开放注册，来源配额阻止连续新登记，关闭注册仍允许已有实例使用。
    """
    async def scenario(client, _factory, _delivery, _redis):
        """
        验证默认关闭、格式约束和按小时配额，所有出错响应都不包含发送密钥。
        """
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", False)
        monkeypatch.setattr(settings, "PUSH_REGISTRATION_PER_HOUR", 1)
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).status_code == 403
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", True)
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth('short'))).status_code == 401
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).status_code == 200
        rejected = await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_B))
        assert rejected.status_code == 429 and rejected.headers['Retry-After'] == '3600'
        assert AUTO_B not in rejected.text
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", False)
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).status_code == 200
        assert (await client.get('/push/devices', params={"owner": OWNER_A}, headers=auth(AUTO_A))).status_code == 200
    run_scenario(monkeypatch, tmp_path, scenario)


def test_revocation_denies_old_keys_and_web_capabilities_but_allows_unbinding(monkeypatch, tmp_path):
    """
    停用既阻止后端发送，也阻止旧网页能力发送测试及消费未用配对码。
    """
    async def scenario(client, _factory, _delivery, _redis):
        """
        同一停用状态作用于两种推送通道，解绑能力仍可清理自己的设备。
        """
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", True)
        await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))
        pair_body = {"owner": OWNER_A, "origin": "https://mp.test", "installation": "app-installation-01"}
        code = (await client.post('/push/pairings', json=pair_body, headers=auth(AUTO_A))).json()['code']
        web = {"code": code, "installation": "browser-installation-01", "subscription": {
            "endpoint": "https://web.push.apple.com/device/example", "keys": {"p256dh": "c" * 87, "auth": "d" * 22}}}
        binding = (await client.post('/push/subscriptions', json=web)).json()
        pending = (await client.post('/push/pairings', json=pair_body, headers=auth(AUTO_A))).json()['code']
        assert (await client.request('DELETE', '/push/instances', headers=auth(AUTO_A))).json() == {"revoked": True}
        assert (await client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A))).status_code == 401
        assert (await client.put('/push/instances/credentials', json={"key": AUTO_B}, headers=auth(AUTO_A))).status_code == 401
        management = {"id": binding['id'], "secret": binding['secret']}
        assert (await client.post('/push/subscriptions/test', json=management)).status_code == 403
        assert (await client.post('/push/subscriptions', json={**web, "code": pending})).status_code == 403
        assert (await client.request('DELETE', '/push/subscriptions', json=management)).status_code == 200
    run_scenario(monkeypatch, tmp_path, scenario)


def test_concurrent_retries_create_only_one_instance(monkeypatch, tmp_path):
    """
    同一持久凭据的并发首次请求只创建一份中心空间，不重复消耗注册配额。
    """
    async def scenario(client, factory, _delivery, _redis):
        """
        并发通过独立请求会话验证分布式锁和唯一约束。
        """
        monkeypatch.setattr(settings, "PUSH_AUTO_REGISTER", True)
        monkeypatch.setattr(settings, "PUSH_REGISTRATION_PER_HOUR", 1)
        replies = await asyncio.gather(*(client.post('/push/instances', json={"user_uid": UID}, headers=auth(AUTO_A)) for _ in range(4)))
        assert all(reply.status_code == 200 for reply in replies)
        assert len({reply.json()['instance'] for reply in replies}) == 1
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(PushRelay)) == 1
    run_scenario(monkeypatch, tmp_path, scenario)


def test_only_center_super_admin_can_inspect_or_disable_instances(monkeypatch):
    """
    运营列表不泄漏密钥，停用需要超级管理员、CSRF和确认，修改留审计。
    """
    from test_admin import login, run_scenario as admin_scenario
    from app.models.admin import AdminAudit, AdminUser

    async def scenario(client, factory, _redis):
        """
        使用真实后台会话测试匿名、CSRF、确认文字和降权后的拒绝。
        """
        stamp, instance = int(time.time()), "a" * 32
        async with factory() as db:
            db.add(PushRelay(id=instance, user_uid=UID, enabled=True, created_at=stamp, updated_at=stamp))
            db.add(PushRelayKey(digest=digest(AUTO_A), relay=instance, active=True, replacement="", created_at=stamp))
            await db.commit()
        path = '/admin/api/push/instances'
        assert (await client.get(path)).status_code == 401
        headers = await login(client)
        listed = await client.get(path, params={"q": UID})
        assert listed.status_code == 200 and listed.json()['total'] == 1
        assert all(value not in listed.text for value in (UID, AUTO_A, digest(AUTO_A), 'destination'))
        payload = {"enabled": False, "confirm": "停用"}
        assert (await client.patch(path + '/' + instance, json=payload)).status_code == 403
        assert (await client.patch(path + '/' + instance, json={**payload, "confirm": ""}, headers=headers)).status_code == 422
        assert (await client.patch(path + '/' + instance, json=payload, headers=headers)).status_code == 200
        async with factory() as db:
            assert (await db.get(PushRelay, instance)).enabled is False
            audit = (await db.scalars(select(AdminAudit).where(AdminAudit.action == 'push.disable'))).all()
            assert len(audit) == 1 and AUTO_A not in json.dumps(audit[0].detail)
            root = await db.scalar(select(AdminUser).where(AdminUser.username == 'root'))
            root.role = 'admin'
            await db.commit()
        assert (await client.get(path)).status_code == 403
        assert (await client.patch(path + '/' + instance, json=payload, headers=headers)).status_code == 403
    admin_scenario(monkeypatch, scenario)
