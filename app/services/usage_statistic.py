"""
安装版本统计服务
"""
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.models import (
    AndroidUsageStatistics,
    HarmonyUsageStatistics,
    IOSUsageStatistics,
    UsageStatistics,
)
from app.schemas.models import (
    AppUsageStatisticItem,
    IOSUsageStatisticItem,
    UsageStatisticItem,
)


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
    def _normalize_app_version_label(version: Any) -> str:
        """统一各端 App 版本展示口径（例如将 v2.0.7 与 2.0.7 归一为 2.0.7）。"""
        raw = str(version or "").strip()
        if not raw:
            return "unknown"
        return re.sub(r"^[vV](?=\d)", "", raw)

    @staticmethod
    def _merge_app_version_counts(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并 iOS / Android / HarmonyOS 全渠道 App 安装版本计数并按安装量倒序排列。"""
        merged: dict[str, int] = {}
        for group in groups:
            for item in group:
                count = int(item.get("count") or 0)
                if count <= 0:
                    continue
                version = UsageService._normalize_app_version_label(item.get("version"))
                if version == "unknown":
                    continue
                merged[version] = merged.get(version, 0) + count
        return [
            {"version": version, "count": count}
            for version, count in sorted(merged.items(), key=lambda pair: (-pair[1], pair[0]))
        ]

    @staticmethod
    def _should_record_usage(usage: UsageStatisticItem) -> bool:
        """
        判断上报是否同时包含用户 ID 和至少一个版本号。
        """
        user_uid = (usage.user_uid or "").strip()
        backend_version = (usage.backend_version or "").strip()
        frontend_version = (usage.frontend_version or "").strip()
        return bool(user_uid and (backend_version or frontend_version))

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
    ) -> Dict[str, Any]:
        """上报安装版本统计"""
        if not UsageService._should_record_usage(usage):
            return {"code": 0, "message": "success"}

        now = UsageService._now()
        try:
            await UsageService._record_usage(db, usage, now)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await UsageService._record_usage(db, usage, now)
            await db.commit()

        UsageService._invalidate_statistics_cache()
        return {"code": 0, "message": "success"}

    @staticmethod
    async def _report_app_usage(
            db: AsyncSession,
            model_cls,
            usage: AppUsageStatisticItem,
    ) -> Dict[str, Any]:
        """保存匿名移动端安装版本，不改变后端实例和活跃用户计数。"""
        await model_cls.record(db, str(usage.installation_id), usage.version, UsageService._now())
        await db.commit()
        UsageService._invalidate_statistics_cache()
        return {"code": 0, "message": "success"}

    @staticmethod
    async def report_ios_usage(db: AsyncSession, usage: IOSUsageStatisticItem) -> Dict[str, Any]:
        """保存匿名 iOS 安装版本，不改变后端实例和活跃用户计数。"""
        return await UsageService._report_app_usage(db, IOSUsageStatistics, usage)

    @staticmethod
    async def report_android_usage(db: AsyncSession, usage: AppUsageStatisticItem) -> Dict[str, Any]:
        """保存匿名 Android 安装版本，不改变后端实例和活跃用户计数。"""
        return await UsageService._report_app_usage(db, AndroidUsageStatistics, usage)

    @staticmethod
    async def report_harmony_usage(db: AsyncSession, usage: AppUsageStatisticItem) -> Dict[str, Any]:
        """保存匿名 HarmonyOS 安装版本，不改变后端实例和活跃用户计数。"""
        return await UsageService._report_app_usage(db, HarmonyUsageStatistics, usage)

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
        ios_app_versions = [
            UsageService._normalize_version_count(row)
            for row in await IOSUsageStatistics.list_version_counts(db)
        ]
        android_app_versions = [
            UsageService._normalize_version_count(row)
            for row in await AndroidUsageStatistics.list_version_counts(db)
        ]
        harmony_app_versions = [
            UsageService._normalize_version_count(row)
            for row in await HarmonyUsageStatistics.list_version_counts(db)
        ]
        app_versions = UsageService._merge_app_version_counts(
            ios_app_versions,
            android_app_versions,
            harmony_app_versions,
        )
        cached_data = {
            "total_users": await UsageStatistics.count_all(db),
            "app_total_users": sum(item["count"] for item in app_versions),
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
            "app_versions": app_versions,
            "ios_app_versions": ios_app_versions,
            "android_app_versions": android_app_versions,
            "harmony_app_versions": harmony_app_versions,
            "updated_at": UsageService._now(),
            "cache_ttl": 1800,
        }
        cache_manager.usage_statistic_cache.set(cache_key, cached_data)
        return cached_data
