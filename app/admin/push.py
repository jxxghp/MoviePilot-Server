"""
中心运营者管理推送实例，仅公开脱敏来源及状态，不提供密钥或设备地址。
"""

import time
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.models.admin import AdminUser
from app.models.push import PushDevice, PushRelay
from app.services.push.registry import binding_lock


async def list_relays(db: AsyncSession, query: str, page: int, size: int) -> dict:
    """
    按既有 UID 或实例标识分页检索，结果没有密钥摘要、原始 UID 或推送令牌。
    """
    statement = select(PushRelay)
    if query:
        statement = statement.where(or_(PushRelay.user_uid.icontains(query, autoescape=True),
                                         PushRelay.id.icontains(query, autoescape=True)))
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    relays = (await db.scalars(statement.order_by(PushRelay.created_at.desc(), PushRelay.id)
                              .offset((page - 1) * size).limit(size))).all()
    counts = dict((await db.execute(select(PushDevice.namespace, func.count()).where(
        PushDevice.namespace.in_([relay.id for relay in relays]),
    ).group_by(PushDevice.namespace))).all()) if relays else {}
    return {"items": [{"id": relay.id, "user_uid": relay.user_uid[:4] + "…" + relay.user_uid[-4:],
                       "enabled": relay.enabled, "devices": counts.get(relay.id, 0),
                       "created_at": datetime.fromtimestamp(relay.created_at, timezone.utc).isoformat(),
                       "updated_at": datetime.fromtimestamp(relay.updated_at, timezone.utc).isoformat()} for relay in relays],
            "total": total or 0, "page": page, "size": size}


async def set_enabled(db: AsyncSession, instance: str, enabled: bool, actor: AdminUser) -> dict:
    """
    与凭据轮换使用同一实例锁，提交状态变更并写入审计；密钥不进入审计。
    """
    async with binding_lock(instance, "instance", "credentials"):
        relay = await db.get(PushRelay, instance, populate_existing=True)
        if not relay:
            raise HTTPException(404, "推送实例不存在")
        relay.enabled, relay.updated_at = enabled, int(time.time())
        record(db, actor, "push.enable" if enabled else "push.disable", instance)
        await db.commit()
    return {"ok": True}
