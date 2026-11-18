"""
跨 worker 检索配额与不含正文的短期随机引用；Redis 不可用时不绕过控制。
"""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from app.db.redis import get_redis

REFERENCE_SECONDS = 600
CLIENT_PER_MINUTE = 60
ADDRESS_PER_MINUTE = 120


def redis():
    """
    缺少 Redis 初始化与网络故障都不能退化为无配额、无限期引用。
    """
    try:
        return get_redis()
    except RuntimeError:
        raise HTTPException(503, "knowledge_state_unavailable") from None


async def quota(identity: str, maximum: int) -> None:
    """
    固定窗口仅保存调用方摘要与计数，不保存查询、令牌或知识正文。
    """
    key = "knowledge:rate:" + hashlib.sha256(identity.encode()).hexdigest() + ":" + str(int(time.time()) // 60)
    async with redis().pipeline(transaction=True) as pipeline:
        pipeline.incr(key)
        pipeline.expire(key, 120)
        results = await pipeline.execute()
    if results[0] > maximum:
        raise HTTPException(429, "knowledge_rate_limited", headers={"Retry-After": "60"})


def reference_key(value: str) -> str:
    """
    引用随机原值只交给调用方，Redis 键也只保存不可逆摘要。
    """
    return "knowledge:reference:" + hashlib.sha256(value.encode()).hexdigest()


async def issue(principal, payloads: list[dict]) -> list[str]:
    """
    为已验证的有限证据签发短期引用，不把原始查询和正文写入 Redis。
    """
    identifiers = ["ke_" + secrets.token_urlsafe(32) for _item in payloads]
    async with redis().pipeline(transaction=True) as pipeline:
        for identifier, payload in zip(identifiers, payloads):
            pipeline.set(reference_key(identifier), json.dumps({
                **payload, "client": principal.client_id, "client_version": principal.version,
            }, separators=(",", ":")), ex=REFERENCE_SECONDS)
        await pipeline.execute()
    return identifiers


async def resolve(principal, identifier: str) -> dict:
    """
    引用不是授权通行证；绑定主体和客户端版本，丢失或到期需重新搜索。
    """
    raw = await redis().get(reference_key(identifier))
    try:
        payload = json.loads(raw) if raw else None
        if (not isinstance(payload, dict) or payload.get("client") != principal.client_id
                or payload.get("client_version") != principal.version):
            raise ValueError("reference_expired")
        return payload
    except (ValueError, TypeError):
        raise HTTPException(410, "knowledge_reference_expired") from None
