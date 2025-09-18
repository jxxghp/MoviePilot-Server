"""
插件相关API路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.models import PluginStatisticList, PluginStatisticItem
from app.services.plugin_statistic import PluginService

router = APIRouter()


@router.post("/install/{pid}")
async def plugin_install(pid: str, payload: PluginStatisticItem | None = None, db: AsyncSession = Depends(get_db)):
    """
    安装插件计数，支持上送repo_url
    """
    repo_url = payload.repo_url if payload else None
    return await PluginService.install_plugin(db, pid, repo_url)


@router.get("/install/{pid}")
async def plugin_install_get(pid: str, db: AsyncSession = Depends(get_db)):
    """
    安装插件计数（兼容GET，不支持repo_url）
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
    查询插件安装统计，包含repo_url
    """
    return await PluginService.get_statistics(db)
