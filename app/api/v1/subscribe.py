"""
订阅相关API路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.deps import get_db
from app.schemas.models import SubscribeStatisticItem, SubscribeStatisticList
from app.services.subscribe import SubscribeService

router = APIRouter()


@router.post("/add")
async def subscribe_add(subscribe: SubscribeStatisticItem, db: AsyncSession = Depends(get_db)):
    """
    添加订阅统计
    """
    return await SubscribeService.add_subscribe(db, subscribe)


@router.post("/done")
async def subscribe_done(subscribe: SubscribeStatisticItem, db: AsyncSession = Depends(get_db)):
    """
    完成订阅更新统计
    """
    return await SubscribeService.done_subscribe(db, subscribe)


@router.post("/report")
async def subscribe_report(subscribes: SubscribeStatisticList, db: AsyncSession = Depends(get_db)):
    """
    批量添加订阅统计
    """
    return await SubscribeService.batch_report_subscribes(db, subscribes.subscribes)


@router.get("/statistic")
async def subscribe_statistic(stype: str, page: int = 1, count: int = 30, genre_id: int = None, db: AsyncSession = Depends(get_db)):
    """
    查询订阅统计
    """
    return await SubscribeService.get_statistics(db, stype, page, count, genre_id)