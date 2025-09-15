"""
订阅分享服务
"""
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.database import SubscribeShare
from app.schemas.models import SubscribeShareItem, SubscribeShareStatisticItem
from app.core.cache import cache_manager
from app.services.tmdb import tmdb_service


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
    async def get_shares(db: AsyncSession, name: str = None, page: int = 1, count: int = 30, genre_id: int = None, min_rating: float = None, max_rating: float = None) -> List[Dict[str, Any]]:
        """查询分享的订阅"""
        cache_key = f"subscribe_{name}_{page}_{count}_{genre_id}_{min_rating}_{max_rating}"
        cached_data = cache_manager.share_cache.get(cache_key)
        
        if not cached_data:
            shares = await SubscribeShare.list(db, name=name, page=page, count=count, genre_id=genre_id, min_rating=min_rating, max_rating=max_rating)
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