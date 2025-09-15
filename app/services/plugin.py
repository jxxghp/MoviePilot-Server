"""
插件统计服务
"""
from typing import Dict, Any

from app.models import PluginStatistics
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager


class PluginService:
    """插件统计服务类"""

    @staticmethod
    async def install_plugin(db: AsyncSession, plugin_id: str) -> Dict[str, Any]:
        """安装插件计数"""
        # 查询数据库中是否存在
        plugin = await PluginStatistics.read(db, plugin_id)

        # 如果不存在则创建
        if not plugin:
            plugin = PluginStatistics(plugin_id=plugin_id, count=1)
            await plugin.create(db)
        # 如果存在则更新
        else:
            await plugin.update(db, {"count": plugin.count + 1})

        return {"code": 0, "message": "success"}

    @staticmethod
    async def batch_install_plugins(db: AsyncSession, plugins: list) -> Dict[str, Any]:
        """批量安装插件计数"""
        for plugin in plugins:
            await PluginService.install_plugin(db, plugin.plugin_id)

        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession) -> Dict[str, int]:
        """查询插件安装统计"""
        cache_key = 'plugin'
        cached_data = cache_manager.statistic_cache.get(cache_key)

        if not cached_data:
            statistics = await PluginStatistics.list(db)
            cached_data = {
                sta.plugin_id: sta.count for sta in statistics
            }
            cache_manager.statistic_cache.set(cache_key, cached_data)

        return cached_data
