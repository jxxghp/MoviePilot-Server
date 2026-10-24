"""
MoviePilot 私有消息中转及主屏幕通知网页接口。
"""

import asyncio
import hmac
import json
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.deps import get_db
from app.models.push import PushDevice, PushRelay
from app.schemas.push import PushEnrollment, PushMessage, PushPairing, PushRegistration, PushRemoval, PushRotation, WebManagement, WebRegistration
from app.services.push.delivery import PushDelivery, push_delivery
from app.services.push.enrollment import authenticated_relay, enroll, rotate
from app.services.push.registry import (
    binding_lock, claim_pairing, create_pairing, devices_for_owner, digest, prune_revision, rate_limit, remove_device, save_device,
)

async def no_store(response: Response) -> None:
    """
    私有推送接口不允许共享代理缓存设备身份、绑定码和管理能力。
    """
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(dependencies=[Depends(no_store)])
bearer = HTTPBearer(auto_error=False)
STATIC = Path(__file__).resolve().parents[1] / "static" / "push"


def get_delivery() -> PushDelivery:
    """
    通过依赖提供进程投递器，测试可以替换真实网络边界。
    """
    return push_delivery


async def relay_namespace(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                          db: AsyncSession = Depends(get_db)) -> str:
    """
    中转密钥只供 MoviePilot 后端使用，每个独立密钥形成独立数据空间。
    """
    await rate_limit("auth:" + request_address(request), 240)
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "需要中转凭据")
    keys = [item.strip() for item in settings.PUSH_RELAY_KEYS.split(",") if len(item.strip()) >= 32]
    supplied = credentials.credentials
    namespace = (digest(supplied) if any(hmac.compare_digest(supplied.encode(), key.encode()) for key in keys)
                 else await authenticated_relay(db, supplied))
    await rate_limit(namespace)
    return namespace


def request_address(request: Request) -> str:
    """
    使用 ASGI 框架已验证的来源，不直接信任任意请求中的转发头。
    """
    return request.client.host if request.client else "unknown"


async def anonymous_limit(request: Request) -> None:
    """
    未绑定接口按实际连接地址限流，不信任客户端自行提交的转发头。
    """
    await rate_limit("anonymous:" + request_address(request), 30)


def enrollment_key(credentials: HTTPAuthorizationCredentials | None) -> str:
    """
    登记和轮换只从请求头提取凭据，缺失时不返回额外身份信息。
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "需要实例凭据")
    return credentials.credentials


@router.post("/instances", dependencies=[Depends(anonymous_limit)])
async def enroll_instance(body: PushEnrollment, request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                          db: AsyncSession = Depends(get_db)):
    """
    幂等登记容器先行保存的随机凭据；相同 UID 不能认领已有空间。
    """
    return await enroll(db, enrollment_key(credentials), body.user_uid, request_address(request))


@router.put("/instances/credentials", dependencies=[Depends(anonymous_limit)])
async def rotate_instance(body: PushRotation, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                          db: AsyncSession = Depends(get_db)):
    """
    原子轮换已登记实例的凭据，响应丢失可凭旧、新凭据重试相同请求。
    """
    return await rotate(db, enrollment_key(credentials), body.key)


@router.delete("/instances")
async def revoke_instance(namespace: str = Depends(relay_namespace), db: AsyncSession = Depends(get_db)):
    """
    当前实例停用自身空间，只有中心运营者能恢复，旧凭据不能自行重新登记。
    """
    relay = await db.get(PushRelay, namespace)
    if not relay:
        raise HTTPException(409, "手工接入凭据请由中心运营者撤销")
    relay.enabled = False
    await db.commit()
    return {"revoked": True}


@router.get("/config")
async def configuration(delivery: PushDelivery = Depends(get_delivery)):
    """
    公开浏览器需要的 VAPID 公钥，绝不下发发送密钥或 Apple 私钥。
    """
    try:
        public_key = await asyncio.to_thread(delivery.public_key)
    except (OSError, ValueError, TypeError):
        public_key = ""
    return {"webpush": bool(public_key), "vapid_public_key": public_key, "automatic_registration": settings.PUSH_AUTO_REGISTER}


@router.post("/devices")
async def register_device(body: PushRegistration, namespace: str = Depends(relay_namespace),
                          db: AsyncSession = Depends(get_db), delivery: PushDelivery = Depends(get_delivery)):
    """
    注册 APNs 设备，topic 必须在服务端允许名单中，不接收任意应用的令牌。
    """
    if not delivery.apns_available() or body.topic not in settings.PUSH_APNS_TOPICS.split(","):
        raise HTTPException(409, "此 App 的 APNs 通道尚未配置，请使用主屏幕通知")
    item = body.model_dump(exclude={"token"})
    item.update(namespace=namespace, transport="apns", destination=body.token.lower(), secret_hash="", parent_installation=body.installation)
    async with binding_lock(namespace, body.owner, body.installation):
        device = await save_device(db, item)
    return {"id": device.id, "transport": "apns"}


@router.delete("/devices")
async def unregister_device(body: PushRemoval, namespace: str = Depends(relay_namespace), db: AsyncSession = Depends(get_db)):
    """
    后端退出或关闭通知时幂等注销当前用户的安装。
    """
    await remove_device(db, namespace, body.owner, body.installation)
    return {"removed": True}


@router.get("/devices")
async def list_devices(owner: str = Query(pattern=r"^[a-zA-Z0-9_-]+$", min_length=16, max_length=64),
                       namespace: str = Depends(relay_namespace), db: AsyncSession = Depends(get_db)):
    """
    只返回绑定标识和通道状态，不泄漏设备令牌或浏览器端点。
    """
    devices = await devices_for_owner(db, namespace, owner)
    return {"devices": [{"id": item.id, "installation": item.installation, "transport": item.transport,
                          "parent_installation": item.parent_installation, "updated_at": item.updated_at} for item in devices]}


@router.post("/pairings")
async def pairing(body: PushPairing, namespace: str = Depends(relay_namespace), delivery: PushDelivery = Depends(get_delivery)):
    """
    后端为已认证用户生成网页绑定码，不要求网页持有后端账号或发送密钥。
    """
    if not await asyncio.to_thread(delivery.public_key):
        raise HTTPException(409, "主屏幕通知尚未配置")
    return {"code": await create_pairing(namespace, body.owner, body.origin, body.installation), "expires_in": 600}


@router.post("/subscriptions", dependencies=[Depends(anonymous_limit)])
async def subscribe(body: WebRegistration, db: AsyncSession = Depends(get_db)):
    """
    一次性绑定码原子换取设备绑定，返回的管理令牌只允许操作本设备。
    """
    secret = secrets.token_urlsafe(32)
    async with claim_pairing(body.code) as binding:
        await require_enabled_namespace(db, binding["namespace"])
        device = await save_device(db, {**binding, "installation": body.installation, "transport": "webpush",
                                    "destination": json.dumps(body.subscription.model_dump(exclude_none=True)),
                                        "secret_hash": digest(secret), "topic": "", "environment": ""})
    return {"id": device.id, "secret": secret, "origin": device.origin, "owner": device.owner}


async def managed_device(body: WebManagement, db: AsyncSession) -> PushDevice:
    """
    设备管理密钥只核对摘要，错误标识与错误密钥统一返回不存在。
    """
    device = await db.get(PushDevice, body.id)
    if not device or device.transport != "webpush" or not hmac.compare_digest(device.secret_hash, digest(body.secret)):
        raise HTTPException(404, "通知绑定不存在")
    return device


async def require_enabled_namespace(db: AsyncSession, namespace: str) -> None:
    """
    已吊销实例的网页能力不能继续绑定或发送测试；解绑仍然可用。
    """
    relay = await db.get(PushRelay, namespace) if len(namespace) == 32 else None
    if relay and not relay.enabled:
        raise HTTPException(403, "通知实例已停用")


@router.delete("/subscriptions", dependencies=[Depends(anonymous_limit)])
async def unsubscribe(body: WebManagement, db: AsyncSession = Depends(get_db)):
    """
    主屏幕网页关闭通知后移除服务端绑定，重复请求按不存在处理。
    """
    device = await managed_device(body, db)
    await prune_revision(db, device)
    return {"removed": True}


@router.post("/subscriptions/test", dependencies=[Depends(anonymous_limit)])
async def test_subscription(body: WebManagement, db: AsyncSession = Depends(get_db), delivery: PushDelivery = Depends(get_delivery)):
    """
    仅向本设备发送固定测试通知，不能借此指定别人或提交任意消息。
    """
    device = await managed_device(body, db)
    await require_enabled_namespace(db, device.namespace)
    accepted, gone, reason = await delivery.send(device, PushMessage(owner=device.owner, title="MoviePilot 测试通知", body="通知服务已连接"))
    if gone:
        await prune_revision(db, device)
    return {"accepted": accepted, "reason": reason}


@router.post("/messages")
async def send_message(body: PushMessage, namespace: str = Depends(relay_namespace), db: AsyncSession = Depends(get_db),
                       delivery: PushDelivery = Depends(get_delivery)):
    """
    向一个用户的持久设备逐个投递，明确区分服务接受与手机实际展示。
    """
    devices = await devices_for_owner(db, namespace, body.owner)
    accepted = 0
    failed = []
    results = await asyncio.gather(*(delivery.send(device, body) for device in devices))
    for device, (success, gone, reason) in zip(devices, results):
        accepted += int(success)
        if not success:
            failed.append({"id": device.id, "reason": reason})
        if gone:
            await prune_revision(db, device)
    return {"total": len(devices), "accepted": accepted, "failed": failed}


@router.get("/app/{asset:path}", include_in_schema=False)
async def notification_page(asset: str = ""):
    """
    仅提供明确列出的离线网页资源，禁止目录穿越和缓存绑定响应。
    """
    allowed = {"": "index.html", "index.html": "index.html", "app.js": "app.js", "style.css": "style.css",
               "worker.js": "worker.js", "manifest.webmanifest": "manifest.webmanifest", "icon.svg": "icon.svg",
               "icon.png": "icon.png"}
    if asset not in allowed:
        raise HTTPException(404)
    media = {"worker.js": "text/javascript", "manifest.webmanifest": "application/manifest+json"}
    return FileResponse(STATIC / allowed[asset], media_type=media.get(asset), headers={
        "Cache-Control": "no-cache", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    })
