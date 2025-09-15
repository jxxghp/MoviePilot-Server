"""
订阅统计服务
"""
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.database import SubscribeStatistics
from app.schemas.models import SubscribeStatisticItem
from app.core.cache import cache_manager


class SubscribeService:
    """订阅统计服务类"""
    
    @staticmethod
    async def add_subscribe(db: AsyncSession, subscribe: SubscribeStatisticItem) -> Dict[str, Any]:
        """添加订阅统计"""
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
    async def get_statistics(db: AsyncSession, stype: str, page: int = 1, count: int = 30, genre_id: int = None) -> List[Dict[str, Any]]:
        """查询订阅统计"""
        cache_key = f"subscribe_{stype}_{page}_{count}_{genre_id}"
        cached_data = cache_manager.statistic_cache.get(cache_key)
        
        if not cached_data:
            statistics = await SubscribeStatistics.list(db, stype=stype, page=page, count=count, genre_id=genre_id)
            cached_data = [sta.dict() for sta in statistics]
            cache_manager.statistic_cache.set(cache_key, cached_data)
        
        return cached_data