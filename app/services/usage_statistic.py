"""
安装版本统计服务
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.models import UsageStatistics
from app.schemas.models import UsageStatisticItem
from app.services.request_user_statistic import RequestUserStatisticService

logger = logging.getLogger(__name__)


class UsageService:
    """安装版本统计服务类"""

    _last_statistics_cache_invalidated_at = 0.0
    _statistics_cache_invalidate_interval = 300

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
    def _build_statistics_response(base_data: Dict[str, Any], other_users: int) -> Dict[str, Any]:
        """
        将请求来源统计合并到安装版本统计报表。
        """
        reported_users = base_data.get("reported_users") or 0
        backend_versions = list(base_data.get("backend_versions") or [])
        frontend_versions = list(base_data.get("frontend_versions") or [])

        if other_users > 0:
            backend_versions.append({
                "version": "未知",
                "count": other_users,
            })
            frontend_versions.append({
                "version": "未知",
                "count": other_users,
            })

        return {
            **base_data,
            "total_users": reported_users + other_users,
            "other_users": other_users,
            "backend_versions": backend_versions,
            "frontend_versions": frontend_versions,
        }

    @staticmethod
    def _invalidate_statistics_cache() -> None:
        """
        按固定间隔失效安装版本统计缓存。
        """
        now = time.monotonic()
        if (
                now - UsageService._last_statistics_cache_invalidated_at
                < UsageService._statistics_cache_invalidate_interval
        ):
            return

        cache_manager.usage_statistic_cache.clear()
        UsageService._last_statistics_cache_invalidated_at = now

    @staticmethod
    async def _count_other_users() -> int:
        """
        读取尚未上报安装版本的未知用户数量。
        """
        try:
            return await RequestUserStatisticService.count_other_users()
        except Exception as err:
            logger.warning(f"Count other usage users skipped: {err}")
            return 0

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
    async def report_usage(
            db: AsyncSession,
            usage: UsageStatisticItem,
            request=None,
    ) -> Dict[str, Any]:
        """上报安装版本统计"""
        now = UsageService._now()
        try:
            await UsageService._record_usage(db, usage, now)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await UsageService._record_usage(db, usage, now)
            await db.commit()

        try:
            await RequestUserStatisticService.mark_request_user_reported(request, usage.user_uid)
        except Exception as err:
            logger.warning(f"Mark request user reported skipped: {err}")

        UsageService._invalidate_statistics_cache()
        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession) -> Dict[str, Any]:
        """查询安装版本统计报表"""
        cache_key = "usage_versions"
        cached_data = cache_manager.usage_statistic_cache.get(cache_key)
        if cached_data is not None:
            other_users = await UsageService._count_other_users()
            return UsageService._build_statistics_response(cached_data, other_users)

        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_7_days = now - timedelta(days=7)
        last_30_days = now - timedelta(days=30)

        backend_versions = await UsageStatistics.list_backend_version_counts(db)
        frontend_versions = await UsageStatistics.list_frontend_version_counts(db)
        total_reported_users = await UsageStatistics.count_all(db)
        normalized_backend_versions = [
            UsageService._normalize_version_count(row) for row in backend_versions
        ]
        normalized_frontend_versions = [
            UsageService._normalize_version_count(row) for row in frontend_versions
        ]
        cached_data = {
            "reported_users": total_reported_users,
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
            "backend_versions": normalized_backend_versions,
            "frontend_versions": normalized_frontend_versions,
            "updated_at": UsageService._now(),
            "cache_ttl": 1800,
        }
        cache_manager.usage_statistic_cache.set(cache_key, cached_data)
        other_users = await UsageService._count_other_users()
        return UsageService._build_statistics_response(cached_data, other_users)
