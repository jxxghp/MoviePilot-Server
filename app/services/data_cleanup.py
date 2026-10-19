"""
每日数据清理服务
"""
import asyncio
from datetime import datetime, timedelta
import logging
from typing import Optional
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.redis import get_redis
from app.models import PluginStatistics, SubscribeShare, SubscribeStatistics
from app.admin.audit import record
from app.admin.invalidation import admin_cache_invalidation

logger = logging.getLogger(__name__)


class DataCleanupService:
    """
    每天凌晨清理历史脏数据。
    """

    _LOCK_TTL_SECONDS = 6 * 60 * 60

    def __init__(self):
        """初始化清理任务句柄。"""
        self._task: Optional[asyncio.Task] = None
        self.next_run: Optional[datetime] = None

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
            self.next_run = next_run
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

    async def cleanup_once(self, actor: str = "scheduler") -> dict:
        """
        执行一次清理。
        """
        plugin_deleted = 0
        share_deleted = 0
        subscribe_deleted = 0
        lock_token: Optional[str] = None

        try:
            lock_token = await self._acquire_cleanup_lock()
            if not lock_token:
                logger.info("Daily data cleanup skipped because another worker is running it")
                return {"status": "skipped", "message": "另一个清理任务正在执行"}

            async with AsyncSessionLocal() as db:
                try:
                    counts = []
                    for model, condition in self.cleanup_conditions():
                        result = await db.execute(delete(model).where(condition))
                        counts.append(result.rowcount or 0)
                    plugin_deleted, share_deleted, subscribe_deleted = counts
                    record(db, actor, "maintenance.cleanup", "success", {
                        "plugins": plugin_deleted, "shares": share_deleted, "subscriptions": subscribe_deleted,
                    })
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

            if plugin_deleted or subscribe_deleted:
                cache_manager.statistic_cache.clear()
            if share_deleted:
                cache_manager.share_cache.clear()
            if plugin_deleted or share_deleted or subscribe_deleted:
                await admin_cache_invalidation.invalidate()

            logger.info(
                "Daily data cleanup finished: plugin_statistics=%s, subscribe_share=%s, subscribe_statistics=%s",
                plugin_deleted,
                share_deleted,
                subscribe_deleted,
            )
            return {"status": "success", "plugins": plugin_deleted, "shares": share_deleted, "subscriptions": subscribe_deleted}
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.exception("Daily data cleanup failed: %s", err)
            try:
                async with AsyncSessionLocal() as db:
                    record(db, actor, "maintenance.cleanup", "failed")
                    await db.commit()
            except Exception:
                # 数据库本身故障时保留调度循环，避免因审计写入再次失败永久停调。
                logger.warning("Cleanup failure audit could not be persisted")
            return {"status": "failed", "message": "清理失败，请查看服务端日志"}
        finally:
            if lock_token:
                await self._release_cleanup_lock(lock_token)

    @staticmethod
    def cleanup_conditions() -> list[tuple]:
        """预览与执行共享同一组筛选条件，避免确认数量与实际规则不一致。"""
        plugin_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        share_cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
        year_value = func.trim(SubscribeStatistics.year)
        return [
            (PluginStatistics, and_(func.coalesce(PluginStatistics.count, 0) < 10, or_(
                PluginStatistics.date.is_(None), func.trim(PluginStatistics.date) == "", PluginStatistics.date < plugin_cutoff,
            ))),
            (SubscribeShare, and_(SubscribeShare.date.is_not(None), func.trim(SubscribeShare.date) != "", SubscribeShare.date < share_cutoff)),
            (SubscribeStatistics, or_(func.coalesce(SubscribeStatistics.count, 0) < 100, and_(
                SubscribeStatistics.year.is_not(None), year_value != "", year_value < "2000",
            ))),
        ]

    async def preview(self, db: AsyncSession) -> dict:
        """只统计符合规则的行数，不执行删除。"""
        counts = []
        for model, condition in self.cleanup_conditions():
            counts.append(await db.scalar(select(func.count()).select_from(model).where(condition)))
        return dict(zip(("plugins", "shares", "subscriptions"), counts))

    @staticmethod
    def _cleanup_lock_key() -> str:
        """
        获取多进程清理任务锁键。
        """
        return f"{settings.REDIS_KEY_PREFIX}:data_cleanup:lock"

    async def _acquire_cleanup_lock(self) -> Optional[str]:
        """
        获取清理任务分布式锁。
        """
        lock_token = str(uuid4())
        acquired = await get_redis().set(
            self._cleanup_lock_key(),
            lock_token,
            ex=self._LOCK_TTL_SECONDS,
            nx=True,
        )
        return lock_token if acquired else None

    async def _release_cleanup_lock(self, lock_token: str):
        """
        释放当前 worker 持有的清理任务分布式锁。
        """
        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
        try:
            await get_redis().eval(release_script, 1, self._cleanup_lock_key(), lock_token)
        except Exception as err:
            logger.warning("Daily data cleanup lock release failed: %s", err)


data_cleanup_service = DataCleanupService()
