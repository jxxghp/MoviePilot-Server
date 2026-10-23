"""
持久保存推送绑定，使用一次性配对码连接自签 App 与主屏幕网页。
"""

import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from redis.exceptions import LockError

from app.db.redis import get_redis
from app.models.push import PushDevice


def digest(value: str) -> str:
    """
    使用不可逆摘要标识能力令牌，数据库与 Redis 键均不记录原始值。
    """
    return hashlib.sha256(value.encode()).hexdigest()


async def rate_limit(key: str, maximum: int = 60) -> None:
    """
    多 worker 共用 Redis 分钟配额，过载时拒绝而不是绕过配额。
    """
    redis = get_redis()
    bucket = f"push:rate:{digest(key)}:{int(time.time()) // 60}"
    async with redis.pipeline(transaction=True) as pipeline:
        pipeline.incr(bucket)
        pipeline.expire(bucket, 120)
        results = await pipeline.execute()
    if results[0] > maximum:
        raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": "60"})


async def create_pairing(namespace: str, owner: str, origin: str, installation: str) -> str:
    """
    创建十分钟一次性能力码，仅包含已由后端确认的用户和实例身份。
    """
    code = secrets.token_urlsafe(32)
    reverse = f"push:pairs:{namespace}:{owner}:{installation}"
    async with get_redis().pipeline(transaction=True) as pipeline:
        pipeline.set(f"push:pair:{digest(code)}", json.dumps({
            "namespace": namespace, "owner": owner, "origin": origin, "parent_installation": installation,
        }), ex=600)
        pipeline.sadd(reverse, digest(code))
        pipeline.expire(reverse, 600)
        await pipeline.execute()
    return code


async def consume_pairing(code: str) -> dict:
    """
    原子消费配对码；两个浏览器竞争时仅一个成功，失效码不可重放。
    """
    value = await get_redis().getdel(f"push:pair:{digest(code)}")
    if not value:
        raise HTTPException(400, "绑定码已过期或使用，请在 App 中重新生成")
    return json.loads(value)


@asynccontextmanager
async def binding_lock(namespace: str, owner: str, installation: str):
    """
    多 worker 对同一安装串行完成绑定和注销，避免注销后的在途请求重建旧绑定。
    """
    key = digest(f"{namespace}:{owner}:{installation}")
    try:
        async with get_redis().lock(f"push:binding:{key}", timeout=30, blocking_timeout=3):
            yield
    except LockError as error:
        raise HTTPException(409, "通知绑定正在更新，请稍后重试") from error


@asynccontextmanager
async def claim_pairing(code: str):
    """
    先定位安装再持锁消费能力码；注销可同时撤销未消费码和已持久化网页。
    """
    raw = await get_redis().get(f"push:pair:{digest(code)}")
    if not raw:
        raise HTTPException(400, "绑定码已过期或使用，请在 App 中重新生成")
    value = json.loads(raw)
    async with binding_lock(value["namespace"], value["owner"], value["parent_installation"]):
        yield await consume_pairing(code)


async def save_device(db: AsyncSession, values: dict) -> PushDevice:
    """
    同一用户串行检查设备配额，多个安装同时绑定也不能突破数量上限。
    """
    async with binding_lock(values["namespace"], values["owner"], "owner-quota"):
        return await _save_device(db, values)


async def _save_device(db: AsyncSession, values: dict) -> PushDevice:
    """
    原子替换同一安装绑定；端点竞争返回可重试冲突，不遗留半完成绑定。
    """
    values.update(id=secrets.token_hex(16), revision=secrets.token_hex(16), updated_at=int(time.time()))
    destination = json.loads(values["destination"])["endpoint"] if values["transport"] == "webpush" else values["destination"]
    values["fingerprint"] = digest(destination)
    existing = (await db.execute(select(PushDevice.owner).where(
        PushDevice.namespace == values["namespace"], PushDevice.installation == values["installation"],
    ))).scalar_one_or_none()
    count = (await db.execute(select(func.count()).select_from(PushDevice).where(
        PushDevice.namespace == values["namespace"], PushDevice.owner == values["owner"],
    ))).scalar_one()
    if existing != values["owner"] and count >= 20:
        raise HTTPException(409, "当前账号最多绑定二十个通知设备，请先解除旧设备")
    # 同一个系统令牌只归属一个绑定，切换用户或实例后旧账号不能继续向其投递。
    await db.execute(delete(PushDevice).where(PushDevice.transport == values["transport"],
                                            PushDevice.fingerprint == values["fingerprint"],
                                            ~((PushDevice.namespace == values["namespace"]) &
                                              (PushDevice.installation == values["installation"]))))
    statement = pg_insert(PushDevice) if db.bind.dialect.name == "postgresql" else sqlite_insert(PushDevice)
    update = {key: value for key, value in values.items() if key not in {"id", "namespace", "installation"}}
    statement = statement.values(**values).on_conflict_do_update(index_elements=["namespace", "installation"], set_=update)
    try:
        await db.execute(statement)
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, "另一个请求正在绑定此设备，请重新生成绑定码并重试") from error
    return (await db.execute(select(PushDevice).where(
        PushDevice.namespace == values["namespace"], PushDevice.installation == values["installation"],
    ))).scalar_one()


async def remove_device(db: AsyncSession, namespace: str, owner: str, installation: str) -> None:
    """
    注销仅影响当前实例和当前用户，不能按安装标识删除别人的设备。
    """
    async with binding_lock(namespace, owner, installation):
        await _remove_binding(db, namespace, owner, installation)


async def _remove_binding(db: AsyncSession, namespace: str, owner: str, installation: str) -> None:
    """
    在安装锁保护下清理持久设备和尚未消费的配对码。
    """
    await db.execute(delete(PushDevice).where(PushDevice.namespace == namespace,
                                            PushDevice.owner == owner,
                                            ((PushDevice.installation == installation) |
                                             (PushDevice.parent_installation == installation))))
    await db.commit()
    redis = get_redis()
    reverse = f"push:pairs:{namespace}:{owner}:{installation}"
    codes = await redis.smembers(reverse)
    if codes:
        await redis.delete(*(f"push:pair:{code}" for code in codes))
    await redis.delete(reverse)


async def devices_for_owner(db: AsyncSession, namespace: str, owner: str) -> list[PushDevice]:
    """
    只读取精确实例和用户的设备；禁用隐式全用户广播。
    """
    return list((await db.execute(select(PushDevice).where(
        PushDevice.namespace == namespace, PushDevice.owner == owner,
    ).order_by(PushDevice.updated_at.desc()).limit(20))).scalars())


async def prune_revision(db: AsyncSession, device: PushDevice) -> None:
    """
    失效清理带版本条件，旧请求失败不能删除刚刚轮换的新令牌。
    """
    await db.execute(delete(PushDevice).where(PushDevice.id == device.id, PushDevice.revision == device.revision))
    await db.commit()
