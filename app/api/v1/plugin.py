"""
插件相关API路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.models import PluginStatisticList
from app.services.plugin import PluginService

router = APIRouter()


@router.get("/install/{pid}")
async def plugin_install(pid: str, db: AsyncSession = Depends(get_db)):
    """
    安装插件计数
    """
    return await PluginService.install_plugin(db, pid)


@router.post("/install")
async def plugin_batch_install(plugins: PluginStatisticList, db: AsyncSession = Depends(get_db)):
    """
    批量安装插件计数
    """
    return await PluginService.batch_install_plugins(db, plugins.plugins)


@router.get("/statistic")
async def plugin_statistic(db: AsyncSession = Depends(get_db)):
    """
    查询插件安装统计
    """
    return await PluginService.get_statistics(db)
