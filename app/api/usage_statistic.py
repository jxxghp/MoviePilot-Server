"""
安装版本统计API路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.models import UsageStatisticItem
from app.services.usage_statistic import UsageService

router = APIRouter()


@router.post("/report")
async def usage_report(usage: UsageStatisticItem, db: AsyncSession = Depends(get_db)):
    """
    上报安装版本统计
    """
    return await UsageService.report_usage(db, usage)


@router.get("/statistic")
async def usage_statistic(db: AsyncSession = Depends(get_db)):
    """
    查询安装版本统计报表
    """
    return await UsageService.get_statistics(db)
