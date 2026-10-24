"""
中心推送实例的凭据登记、轮换与权限解析；UID 不参与授权决策。
"""

import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.push import PushRelay, PushRelayKey
from app.services.push.registry import binding_lock, digest, rate_limit


def require_automatic_key(key: str) -> str:
    """
    自动凭据固定为带版本前缀的256位随机值；返回不可逆摘要，不回显输入。
    """
    if not re.fullmatch(r"mpa_[a-zA-Z0-9_-]{43}", key):
        raise HTTPException(401, "实例凭据无效")
    return digest(key)


async def authenticated_relay(db: AsyncSession, key: str) -> str:
    """
    每次请求核对当前密钥及实例状态，禁用或轮换立即影响后续请求。
    """
    row = (await db.execute(select(PushRelay.id).join(PushRelayKey, PushRelayKey.relay == PushRelay.id).where(
        PushRelayKey.digest == digest(key), PushRelayKey.active.is_(True), PushRelay.enabled.is_(True),
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "中转凭据无效")
    return row


async def enroll(db: AsyncSession, key: str, user_uid: str, address: str) -> dict:
    """
    幂等登记新凭据；相同 UID 不合并空间，不允许复活吊销凭据。

    :param db: 当前请求会话，本函数提交登记事务
    :param key: 容器先行持久化的随机凭据，仅保存摘要
    :param user_uid: 容器自报的既有唯一标识，不证明自然人身份
    :param address: 框架解析的实际请求来源，用于新注册限流
    :return: 稳定实例标识，不包含密钥或其摘要
    """
    fingerprint = require_automatic_key(key)
    async with binding_lock(fingerprint, "instance", "enrollment"):
        prior = await db.get(PushRelayKey, fingerprint)
        if prior:
            namespace = await authenticated_relay(db, key)
            return {"instance": namespace, "registered": True}
        if not settings.PUSH_AUTO_REGISTER:
            raise HTTPException(403, "中心服务尚未开放自动接入，请联系运营者")
        await rate_limit("enroll:ip:" + address, max(settings.PUSH_REGISTRATION_PER_HOUR, 1), 3600)
        await rate_limit("enroll:global", max(settings.PUSH_REGISTRATION_GLOBAL_PER_HOUR, 1), 3600)
        stamp = int(time.time())
        relay = PushRelay(id=secrets.token_hex(16), user_uid=user_uid, enabled=True, created_at=stamp, updated_at=stamp)
        db.add(relay)
        db.add(PushRelayKey(digest=fingerprint, relay=relay.id, active=True, replacement="", created_at=stamp))
        try:
            await db.commit()
        except IntegrityError as error:
            await db.rollback()
            raise HTTPException(409, "实例登记发生冲突，请重试") from error
        return {"instance": relay.id, "registered": True}


async def rotate(db: AsyncSession, old_key: str, new_key: str) -> dict:
    """
    保留空间及设备并原子替换密钥，旧密钥只能重试完全相同的已成功轮换。
    """
    old_hash, new_hash = require_automatic_key(old_key), require_automatic_key(new_key)
    if old_hash == new_hash:
        raise HTTPException(422, "新凭据必须不同于当前凭据")
    old = await db.get(PushRelayKey, old_hash)
    if old is None:
        raise HTTPException(401, "中转凭据无效")
    namespace = old.relay
    async with binding_lock(namespace, "instance", "credentials"):
        await db.refresh(old)
        relay = await db.get(PushRelay, namespace, populate_existing=True)
        if not relay or not relay.enabled:
            raise HTTPException(401, "中转凭据无效")
        replacement = await db.get(PushRelayKey, new_hash, populate_existing=True)
        if not old.active:
            if old.replacement == new_hash and replacement and replacement.relay == namespace and replacement.active:
                return {"instance": namespace, "rotated": True}
            raise HTTPException(401, "中转凭据无效")
        if replacement:
            raise HTTPException(409, "新凭据不可用，请重新生成")
        await rate_limit("rotate:" + namespace, 20, 3600)
        old.active, old.replacement = False, new_hash
        relay.updated_at = int(time.time())
        db.add(PushRelayKey(digest=new_hash, relay=namespace, active=True, replacement="", created_at=relay.updated_at))
        try:
            await db.commit()
        except IntegrityError as error:
            await db.rollback()
            raise HTTPException(409, "凭据轮换发生冲突，请重试") from error
        return {"instance": namespace, "rotated": True}
