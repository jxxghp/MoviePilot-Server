"""
每日数据清理服务
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.db.database import AsyncSessionLocal
from app.models import PluginStatistics, SubscribeShare, SubscribeStatistics

logger = logging.getLogger(__name__)


class DataCleanupService:
    """
    每天凌晨清理历史脏数据。
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """
        启动定时清理任务。
        """
        if self._task and not self._task.done():
            return

        self._task = asyncio.create_task(self._run_scheduler(), name="daily-data-cleanup")
        logger.info("Daily data cleanup scheduler started")

    async def stop(self):
        """
        停止定时清理任务。
        """
        if not self._task:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

        logger.info("Daily data cleanup scheduler stopped")

    async def _run_scheduler(self):
        """
        每天凌晨触发一次清理。
        """
        while True:
            next_run = self._next_midnight()
            delay = max((next_run - datetime.now()).total_seconds(), 1)
            logger.info("Next daily data cleanup scheduled at %s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
            await asyncio.sleep(delay)
            await self.cleanup_once()

    @staticmethod
    def _next_midnight() -> datetime:
        """
        计算下一个本地凌晨时间。
        """
        now = datetime.now()
        return (now + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

    async def cleanup_once(self):
        """
        执行一次清理。
        """
        plugin_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        share_cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
        plugin_deleted = 0
        share_deleted = 0
        subscribe_deleted = 0

        try:
            async with AsyncSessionLocal() as db:
                try:
                    plugin_deleted = await self._cleanup_plugin_statistics(db, plugin_cutoff)
                    share_deleted = await self._cleanup_subscribe_share(db, share_cutoff)
                    subscribe_deleted = await self._cleanup_subscribe_statistics(db)
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

            if plugin_deleted or subscribe_deleted:
                cache_manager.statistic_cache.clear()
            if share_deleted:
                cache_manager.share_cache.clear()

            logger.info(
                "Daily data cleanup finished: plugin_statistics=%s, subscribe_share=%s, subscribe_statistics=%s",
                plugin_deleted,
                share_deleted,
                subscribe_deleted,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.exception("Daily data cleanup failed: %s", err)

    @staticmethod
    async def _cleanup_plugin_statistics(db: AsyncSession, plugin_cutoff: str) -> int:
        """
        清理低频且长期未更新的插件统计数据。
        """
        result = await db.execute(
            delete(PluginStatistics).where(
                and_(
                    func.coalesce(PluginStatistics.count, 0) < 10,
                    or_(
                        PluginStatistics.date.is_(None),
                        func.trim(PluginStatistics.date) == "",
                        PluginStatistics.date < plugin_cutoff,
                    ),
                )
            )
        )
        return result.rowcount or 0

    @staticmethod
    async def _cleanup_subscribe_share(db: AsyncSession, share_cutoff: str) -> int:
        """
        清理一年前的订阅分享数据。
        """
        result = await db.execute(
            delete(SubscribeShare).where(
                and_(
                    SubscribeShare.date.is_not(None),
                    func.trim(SubscribeShare.date) != "",
                    SubscribeShare.date < share_cutoff,
                )
            )
        )
        return result.rowcount or 0

    @staticmethod
    async def _cleanup_subscribe_statistics(db: AsyncSession) -> int:
        """
        清理低价值或脏的订阅统计数据。
        """
        year_value = func.trim(SubscribeStatistics.year)
        result = await db.execute(
            delete(SubscribeStatistics).where(
                or_(
                    func.coalesce(SubscribeStatistics.count, 0) < 100,
                    SubscribeStatistics.tmdbid.is_(None),
                    and_(
                        SubscribeStatistics.year.is_not(None),
                        year_value != "",
                        year_value < "2000",
                    ),
                )
            )
        )
        return result.rowcount or 0


data_cleanup_service = DataCleanupService()
