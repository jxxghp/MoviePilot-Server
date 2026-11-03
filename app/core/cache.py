"""
缓存管理
"""
from cacheout import Cache


class CacheManager:
    """缓存管理器"""

    def __init__(self):
        """创建各类本地缓存，配置缓存使用短 TTL 限制跨进程失效延迟。"""
        self.features_cache = Cache(maxsize=1, ttl=3)
        self.features_generation = 0

        # 统计缓存
        self.statistic_cache = Cache(maxsize=128, ttl=1800)

        # 插件评分公共汇总缓存
        self.plugin_rating_cache = Cache(maxsize=2048, ttl=1800)

        # 安装版本统计报表缓存
        self.usage_statistic_cache = Cache(maxsize=32, ttl=1800)

        # 订阅分享缓存
        self.share_cache = Cache(maxsize=128, ttl=1800)

        # 工作流分享缓存
        self.workflow_share_cache = Cache(maxsize=128, ttl=1800)

    def clear_all(self):
        """清除所有缓存，并阻止失效前开始的配置读取重新填入旧值。"""
        self.features_generation += 1
        self.features_cache.clear()
        self.statistic_cache.clear()
        self.plugin_rating_cache.clear()
        self.usage_statistic_cache.clear()
        self.share_cache.clear()
        self.workflow_share_cache.clear()


# 全局缓存实例
cache_manager = CacheManager()
