"""用 Redis 修订号将管理操作的本地缓存失效同步到所有 worker。"""

import asyncio
import logging

from app.core.cache import cache_manager
from app.core.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)


class AdminCacheInvalidation:
    """后台低频轮询修订号，不在高流量公共请求上增加 Redis 查询。"""

    def __init__(self) -> None:
        """初始化轮询任务及网络恢复后的待同步状态。"""
        self.task: asyncio.Task | None = None
        self.pending = False
        self.revision = None

    @staticmethod
    def key() -> str:
        """缓存修订号使用独立命名空间，不触及业务缓存键。"""
        return f"{settings.REDIS_KEY_PREFIX}:admin:cache_revision"

    async def invalidate(self) -> None:
        """提交后立即失效当前 worker，并通知其他 worker；连接恢复后重试。"""
        cache_manager.clear_all()
        self.pending = True
        try:
            await get_redis().incr(self.key())
            self.pending = False
        except Exception:
            logger.warning("Admin cache revision sync pending Redis recovery")

    async def start(self) -> None:
        """每个应用 worker 启动一个轻量缓存同步任务。"""
        self.task = asyncio.create_task(self._watch(), name="admin-cache-revision")

    async def stop(self) -> None:
        """关闭应用时取消轮询并等待退出。"""
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _watch(self) -> None:
        """重连后仍比较持久修订号，避免仅靠 pub/sub 丢失失效通知。"""
        while True:
            try:
                if self.pending:
                    await self.invalidate()
                revision = await get_redis().get(self.key())
                if revision != self.revision:
                    cache_manager.clear_all()
                    self.revision = revision
            except Exception:
                # 公共服务已有 Redis 故障处理，此任务无需重复放大日志。
                pass
            await asyncio.sleep(3)


admin_cache_invalidation = AdminCacheInvalidation()
