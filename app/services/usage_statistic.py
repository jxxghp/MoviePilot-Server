"""
安装版本统计服务
"""
from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.models import UsageStatistics
from app.schemas.models import UsageStatisticItem


class UsageService:
    """安装版本统计服务类"""

    @staticmethod
    def _now() -> str:
        """
        获取统一格式的当前时间字符串。
        """
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_version_count(row) -> dict:
        """
        将版本统计查询结果转换为前端需要的字典结构。
        """
        version, count = row
        return {
            "version": version or "unknown",
            "count": count or 0,
        }

    @staticmethod
    async def _record_usage(db: AsyncSession, usage: UsageStatisticItem, now: str) -> None:
        """
        记录单个安装用户的最新版本信息。
        """
        payload = usage.model_dump(exclude={"user_uid"})
        updated = await UsageStatistics.upsert_by_user_uid(
            db=db,
            user_uid=usage.user_uid,
            payload=payload,
            now=now,
        )
        if updated:
            return

        db.add(
            UsageStatistics(
                **usage.model_dump(),
                first_seen_at=now,
                last_seen_at=now,
                report_count=1,
            )
        )

    @staticmethod
    async def report_usage(db: AsyncSession, usage: UsageStatisticItem) -> Dict[str, Any]:
        """上报安装版本统计"""
        now = UsageService._now()
        try:
            await UsageService._record_usage(db, usage, now)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await UsageService._record_usage(db, usage, now)
            await db.commit()

        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession) -> Dict[str, Any]:
        """查询安装版本统计报表"""
        cache_key = "usage_versions"
        cached_data = cache_manager.usage_statistic_cache.get(cache_key)
        if cached_data is not None:
            return cached_data

        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_7_days = now - timedelta(days=7)
        last_30_days = now - timedelta(days=30)

        backend_versions = await UsageStatistics.list_backend_version_counts(db)
        frontend_versions = await UsageStatistics.list_frontend_version_counts(db)
        cached_data = {
            "total_users": await UsageStatistics.count_all(db),
            "active_users": {
                "today": await UsageStatistics.count_active_since(
                    db, today.strftime("%Y-%m-%d %H:%M:%S")
                ),
                "last_7_days": await UsageStatistics.count_active_since(
                    db, last_7_days.strftime("%Y-%m-%d %H:%M:%S")
                ),
                "last_30_days": await UsageStatistics.count_active_since(
                    db, last_30_days.strftime("%Y-%m-%d %H:%M:%S")
                ),
            },
            "backend_versions": [
                UsageService._normalize_version_count(row) for row in backend_versions
            ],
            "frontend_versions": [
                UsageService._normalize_version_count(row) for row in frontend_versions
            ],
            "updated_at": UsageService._now(),
            "cache_ttl": 3600,
        }
        cache_manager.usage_statistic_cache.set(cache_key, cached_data)
        return cached_data
