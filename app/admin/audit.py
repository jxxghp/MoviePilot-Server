"""统一生成安全的后台审计记录。"""

import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminAudit, AdminUser


def now() -> str:
    """审计与账号时间统一使用带时区的 UTC ISO 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(db: AsyncSession, actor: AdminUser | str, action: str, target: str, detail: dict | None = None) -> None:
    """将审计记录加入业务事务，由调用方共同提交或回滚。"""
    db.add(AdminAudit(
        actor=actor.username if isinstance(actor, AdminUser) else actor,
        action=action, target=target[:200], created_at=now(),
        detail=json.dumps(detail or {}, ensure_ascii=False),
    ))


def user_dict(user: AdminUser) -> dict:
    """输出账号可见字段，禁止下发密码哈希和会话版本。"""
    return {key: getattr(user, key) for key in (
        "id", "username", "display_name", "role", "enabled", "must_change_password", "created_at", "last_login_at",
    )}
