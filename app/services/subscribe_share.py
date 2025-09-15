"""
订阅分享服务
"""
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.database import SubscribeShare
from app.schemas.models import SubscribeShareItem, SubscribeShareStatisticItem
from app.core.cache import cache_manager


class SubscribeShareService:
    """订阅分享服务类"""
    
    @staticmethod
    async def create_share(db: AsyncSession, subscribe: SubscribeShareItem) -> Dict[str, Any]:
        """新增订阅分享"""
        if not subscribe.share_title or not subscribe.share_user:
            return {
                "code": 1,
                "message": "请填写分享标题和说明"
            }
        
        # 查询数据库中是否存在
        sub = await SubscribeShare.read(db, title=subscribe.share_title, user=subscribe.share_user)
        
        # 如果不存在则创建
        if not sub:
            subscribe.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sub = SubscribeShare(**subscribe.dict(), count=1)
            await sub.create(db)
        # 如果存在则报错
        else:
            return {
                "code": 2,
                "message": "您使用的昵称已经分享过这个订阅了"
            }
        
        # 清除缓存
        cache_manager.share_cache.clear()
        
        return {"code": 0, "message": "success"}
    
    @staticmethod
    async def delete_share(db: AsyncSession, share_id: int, share_uid: str) -> Dict[str, Any]:
        """删除订阅分享"""
        # 查询数据库中是否存在
        sub = await SubscribeShare.read_by_id(db, share_id)
        
        # 如果存在则删除
        if sub and share_uid:
            await sub.delete(db, share_id)
            # 清除缓存
            cache_manager.share_cache.clear()
            return {"code": 0, "message": "success"}
        
        return {"code": 1, "message": "分享不存在或无权限"}
    
    @staticmethod
    async def get_shares(db: AsyncSession, name: str = None, page: int = 1, count: int = 30) -> List[Dict[str, Any]]:
        """查询分享的订阅"""
        cache_key = f"subscribe_{name}_{page}_{count}"
        cached_data = cache_manager.share_cache.get(cache_key)
        
        if not cached_data:
            shares = await SubscribeShare.list(db, name=name, page=page, count=count)
            cached_data = [sha.dict() for sha in shares]
            cache_manager.share_cache.set(cache_key, cached_data)
        
        return cached_data
    
    @staticmethod
    async def fork_share(db: AsyncSession, share_id: int) -> Dict[str, Any]:
        """复用分享的订阅"""
        # 查询数据库中是否存在
        share = await SubscribeShare.read_by_id(db, sid=share_id)
        
        # 如果存在则更新
        if share:
            await share.update(db, {"count": share.count + 1})
        
        return {"code": 0, "message": "success"}
    
    @staticmethod
    async def get_share_statistics(db: AsyncSession) -> List[Dict[str, Any]]:
        """查询订阅分享统计"""
        cache_key = "subscribe_share_statistics"
        cached_data = cache_manager.share_cache.get(cache_key)
        
        if not cached_data:
            statistics = await SubscribeShare.share_statistics(db)
            cached_data = [
                {
                    "share_user": stat.share_user,
                    "share_count": stat.share_count,
                    "total_reuse_count": stat.total_reuse_count or 0
                } for stat in statistics
            ]
            cache_manager.share_cache.set(cache_key, cached_data)
        
        return cached_data