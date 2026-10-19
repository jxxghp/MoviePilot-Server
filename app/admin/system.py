"""只读服务状态与有预览、确认和审计的维护操作。"""

import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.data import serialize
from app.core.config import settings
from app.db.redis import get_redis
from app.models.admin import AdminAudit
from app.services.data_cleanup import data_cleanup_service


async def status(db: AsyncSession) -> dict:
    """探测基础依赖并展示非敏感配置，不返回连接 URL、密码或 OAuth 令牌。"""
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=3)
        database = "ok"
    except Exception:
        await db.rollback()
        database = "error"
    try:
        await asyncio.wait_for(get_redis().ping(), timeout=3)
        redis = "ok"
    except Exception:
        redis = "error"
    latest = None
    if database == "ok":
        row = await db.scalar(select(AdminAudit).where(AdminAudit.action == "maintenance.cleanup").order_by(AdminAudit.id.desc()).limit(1))
        latest = serialize(row) if row else None
    return {
        "database": database, "redis": redis, "database_type": settings.DATABASE_TYPE,
        "version": settings.APP_VERSION, "workers": settings.server_workers,
        "cookie_secure": settings.ADMIN_COOKIE_SECURE, "session_hours": min(max(settings.ADMIN_SESSION_HOURS, 1), 168),
        "u115": {"client_id_configured": bool(settings.U115_CLIENT_ID),
                 "client_secret_configured": bool(settings.U115_CLIENT_SECRET),
                 "redirect_uri_configured": bool(settings.U115_REDIRECT_URI)},
        "cleanup": {"next_run": data_cleanup_service.next_run.astimezone().isoformat() if data_cleanup_service.next_run else None, "latest": latest,
                    "rules": ["插件：安装次数少于 10 且 30 天未更新（含时间缺失）", "订阅分享：创建超过 365 天", "订阅统计：次数少于 100 或有效年份早于 2000"]},
    }
