"""
插件统计服务
"""
from datetime import datetime
from typing import Dict, Any, List

from app.models import PluginStatistics
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager


class PluginService:
    """插件统计服务类"""

    @staticmethod
    async def _record_install(
            db: AsyncSession,
            plugin_id: str,
            repo_url: str | None = None,
            increment: int = 1,
    ) -> None:
        """
        记录插件安装次数，优先走索引命中的原子更新。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated = await PluginStatistics.increment_count_by_plugin_id(
            db=db,
            pid=plugin_id,
            increment=increment,
            date=now,
            repo_url=repo_url,
        )
        if updated:
            return

        # 兼容历史上大小写混用的 plugin_id；只有精确匹配失败时才走 lower 查询。
        plugin = await PluginStatistics.read_prefer_camel(db, plugin_id)
        if plugin:
            await PluginStatistics.increment_count_by_id(
                db=db,
                sid=plugin.id,
                increment=increment,
                date=now,
                repo_url=repo_url,
            )
            return

        db.add(PluginStatistics(plugin_id=plugin_id, count=increment, repo_url=repo_url, date=now))

    @staticmethod
    async def _commit_install(
            db: AsyncSession,
            plugin_id: str,
            repo_url: str | None = None,
            increment: int = 1,
    ) -> None:
        """
        提交插件安装计数，并在并发插入冲突时回退为原子更新。
        """
        try:
            await PluginService._record_install(db, plugin_id, repo_url, increment)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await PluginService._record_install(db, plugin_id, repo_url, increment)
            await db.commit()

    @staticmethod
    async def install_plugin(db: AsyncSession, plugin_id: str, repo_url: str | None = None) -> Dict[str, Any]:
        """安装插件计数，并可更新仓库地址"""
        await PluginService._commit_install(db, plugin_id, repo_url)

        return {"code": 0, "message": "success"}

    @staticmethod
    async def batch_install_plugins(db: AsyncSession, plugins: List[Any]) -> Dict[str, Any]:
        """批量安装插件计数"""
        aggregated_plugins: dict[str, dict[str, Any]] = {}
        for plugin in plugins:
            plugin_id = getattr(plugin, "plugin_id", None)
            if not plugin_id:
                continue

            item = aggregated_plugins.setdefault(plugin_id, {"increment": 0, "repo_url": None})
            item["increment"] += 1
            repo_url = getattr(plugin, "repo_url", None)
            if repo_url:
                item["repo_url"] = repo_url

        for plugin_id, item in aggregated_plugins.items():
            await PluginService._commit_install(
                db=db,
                plugin_id=plugin_id,
                repo_url=item["repo_url"],
                increment=item["increment"],
            )

        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession) -> Dict[str, int]:
        """查询插件安装统计"""
        cache_key = 'plugin'
        cached_data = cache_manager.statistic_cache.get(cache_key)

        if cached_data is None:
            statistics = await PluginStatistics.list_id_count(db)
            cached_data = {
                row.plugin_id: row.count for row in statistics
            }
            cache_manager.statistic_cache.set(cache_key, cached_data)

        return cached_data
