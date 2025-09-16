"""
订阅统计服务
"""
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.models import SubscribeStatistics
from app.schemas.models import SubscribeStatisticItem, SortType
from app.services.tmdb import tmdb_service


class SubscribeService:
    """订阅统计服务类"""

    @staticmethod
    async def add_subscribe(db: AsyncSession, subscribe: SubscribeStatisticItem) -> Dict[str, Any]:
        """添加订阅统计"""
        # 如果没有genre_ids但有tmdbid，则查询TheMovieDB获取
        if not subscribe.genre_ids and subscribe.tmdbid and subscribe.type:
            try:
                tmdb_info = await tmdb_service.get_media_info(subscribe.tmdbid, subscribe.type)
                if tmdb_info and tmdb_info.get("genre_ids"):
                    subscribe.genre_ids = tmdb_info["genre_ids"]
                    # 同时更新其他可能缺失的信息
                    if not subscribe.name and tmdb_info.get("name"):
                        subscribe.name = tmdb_info["name"]
                    if not subscribe.year and tmdb_info.get("year"):
                        subscribe.year = tmdb_info["year"]
                    if not subscribe.poster and tmdb_info.get("poster"):
                        subscribe.poster = tmdb_info["poster"]
                    if not subscribe.backdrop and tmdb_info.get("backdrop"):
                        subscribe.backdrop = tmdb_info["backdrop"]
                    if not subscribe.vote and tmdb_info.get("vote"):
                        subscribe.vote = tmdb_info["vote"]
                    if not subscribe.description and tmdb_info.get("description"):
                        subscribe.description = tmdb_info["description"]
            except Exception as e:
                print(f"查询TheMovieDB失败: {e}")

        # 查询数据库中是否存在
        sub = await SubscribeStatistics.read(
            db,
            mid=subscribe.tmdbid or subscribe.doubanid,
            season=subscribe.season
        )

        # 如果不存在则创建
        if not sub:
            sub = SubscribeStatistics(**subscribe.dict(), count=1)
            await sub.create(db)
        # 如果存在则更新
        else:
            await sub.update(db, {"count": sub.count + 1})

        return {"code": 0, "message": "success"}

    @staticmethod
    async def done_subscribe(db: AsyncSession, subscribe: SubscribeStatisticItem) -> Dict[str, Any]:
        """完成订阅更新统计"""
        # 查询数据库中是否存在
        sub = await SubscribeStatistics.read(
            db,
            mid=subscribe.tmdbid or subscribe.doubanid,
            season=subscribe.season
        )

        # 如果存在则更新
        if sub:
            if sub.count <= 1:
                await sub.delete(db, sub.id)
            else:
                await sub.update(db, {"count": sub.count - 1})

        return {"code": 0, "message": "success"}

    @staticmethod
    async def batch_report_subscribes(db: AsyncSession, subscribes: List[SubscribeStatisticItem]) -> Dict[str, Any]:
        """批量添加订阅统计"""
        for subscribe in subscribes:
            await SubscribeService.add_subscribe(db, subscribe)

        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession, stype: str, page: int = 1, count: int = 30, genre_id: int = None,
                             min_rating: float = None, max_rating: float = None, sort_type: SortType = SortType.COUNT) -> List[Dict[str, Any]]:
        """查询订阅统计"""
        cache_key = f"subscribe_{stype}_{page}_{count}_{genre_id}_{min_rating}_{max_rating}_{sort_type}"
        cached_data = cache_manager.statistic_cache.get(cache_key)

        if not cached_data:
            statistics = await SubscribeStatistics.list(db, stype=stype, page=page, count=count, genre_id=genre_id,
                                                        min_rating=min_rating, max_rating=max_rating, sort_type=sort_type)
            cached_data = [sta.dict() for sta in statistics]
            cache_manager.statistic_cache.set(cache_key, cached_data)

        return cached_data
