"""
插件统计服务
"""
from datetime import datetime
from typing import Dict, Any, List

from app.models import PluginStatistics
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager


class PluginService:
    """插件统计服务类"""

    @staticmethod
    async def install_plugin(db: AsyncSession, plugin_id: str, repo_url: str | None = None) -> Dict[str, Any]:
        """安装插件计数，并可更新仓库地址"""
        # 查询数据库中是否存在；当存在大小写两条记录时优先更新驼峰记录
        plugin = await PluginStatistics.read_prefer_camel(db, plugin_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 如果不存在则创建
        if not plugin:
            plugin = PluginStatistics(plugin_id=plugin_id, count=1, repo_url=repo_url, date=now)
            await plugin.create(db)
        # 如果存在则更新
        else:
            payload = {"count": plugin.count + 1, "date": now}
            if repo_url:
                payload["repo_url"] = repo_url
            await plugin.update(db, payload)

        return {"code": 0, "message": "success"}

    @staticmethod
    async def batch_install_plugins(db: AsyncSession, plugins: List[Any]) -> Dict[str, Any]:
        """批量安装插件计数"""
        for plugin in plugins:
            # plugin is Pydantic item with attributes
            await PluginService.install_plugin(db, plugin.plugin_id, getattr(plugin, "repo_url", None))

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
