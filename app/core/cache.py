"""
缓存管理
"""
from cacheout import Cache


class CacheManager:
    """缓存管理器"""

    def __init__(self):
        # 统计缓存
        self.statistic_cache = Cache(maxsize=128, ttl=1800)

        # 订阅分享缓存
        self.share_cache = Cache(maxsize=128, ttl=1800)

        # 工作流分享缓存
        self.workflow_share_cache = Cache(maxsize=128, ttl=1800)

    def clear_all(self):
        """清除所有缓存"""
        self.statistic_cache.clear()
        self.share_cache.clear()
        self.workflow_share_cache.clear()


# 全局缓存实例
cache_manager = CacheManager()
