"""
插件统计服务
"""
from typing import Dict, Any, List

from app.models import PluginStatistics
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager


class PluginService:
    """插件统计服务类"""

    @staticmethod
    async def install_plugin(db: AsyncSession, plugin_id: str, repo_url: str | None = None) -> Dict[str, Any]:
        """安装插件计数，并可更新仓库地址"""
        await PluginStatistics.ensure_repo_url_column(db)
        # 查询数据库中是否存在
        plugin = await PluginStatistics.read(db, plugin_id)

        # 如果不存在则创建
        if not plugin:
            plugin = PluginStatistics(plugin_id=plugin_id, count=1, repo_url=repo_url)
            await plugin.create(db)
        # 如果存在则更新
        else:
            payload = {"count": plugin.count + 1}
            if repo_url:
                payload["repo_url"] = repo_url
            await plugin.update(db, payload)

        return {"code": 0, "message": "success"}

    @staticmethod
    async def batch_install_plugins(db: AsyncSession, plugins: List[Any]) -> Dict[str, Any]:
        """批量安装插件计数"""
        await PluginStatistics.ensure_repo_url_column(db)
        for plugin in plugins:
            # plugin is Pydantic item with attributes
            await PluginService.install_plugin(db, plugin.plugin_id, getattr(plugin, "repo_url", None))

        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession) -> List[Dict[str, Any]]:
        """查询插件安装统计，包含仓库地址"""
        cache_key = 'plugin'
        cached_data = cache_manager.statistic_cache.get(cache_key)

        if not cached_data:
            await PluginStatistics.ensure_repo_url_column(db)
            statistics = await PluginStatistics.list(db)
            # Normalize to list of dicts
            cached_data = [
                {"plugin_id": sta.plugin_id, "count": sta.count, "repo_url": getattr(sta, "repo_url", None)}
                for sta in statistics
            ]
            cache_manager.statistic_cache.set(cache_key, cached_data)

        return cached_data
