"""
订阅分享相关API路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.deps import get_db
from app.schemas.models import SubscribeShareItem
from app.services.subscribe_share import SubscribeShareService

router = APIRouter()


@router.post("/share")
async def subscribe_share(subscribe: SubscribeShareItem, db: AsyncSession = Depends(get_db)):
    """
    新增订阅分享
    """
    return await SubscribeShareService.create_share(db, subscribe)


@router.get("/share/statistics")
async def subscribe_share_statistics(db: AsyncSession = Depends(get_db)):
    """
    查询订阅分享统计
    返回每个分享人分享的媒体数量以及总的复用人次
    """
    return await SubscribeShareService.get_share_statistics(db)


@router.delete("/share/{sid}")
async def subscribe_share_delete(sid: int, share_uid: str, db: AsyncSession = Depends(get_db)):
    """
    删除订阅分享
    """
    return await SubscribeShareService.delete_share(db, sid, share_uid)


@router.get("/shares")
async def subscribe_shares(name: str = None, page: int = 1, count: int = 30, genre_id: int = None, db: AsyncSession = Depends(get_db)):
    """
    查询分享的订阅
    """
    return await SubscribeShareService.get_shares(db, name, page, count, genre_id)


@router.get("/fork/{shareid}")
async def subscribe_fork(shareid: int, db: AsyncSession = Depends(get_db)):
    """
    复用分享的订阅
    """
    return await SubscribeShareService.fork_share(db, shareid)