"""
API路由汇总
"""
from fastapi import APIRouter

from app.api import plugin_statistic, subscribe_statistic, subscribe_share, workflow_share, site_config

api_router = APIRouter()

# 包含各个模块的路由
api_router.include_router(plugin_statistic.router, prefix="/plugin", tags=["plugin-statistic"])
api_router.include_router(subscribe_statistic.router, prefix="/subscribe", tags=["subscribe-statistic"])
api_router.include_router(subscribe_share.router, prefix="/subscribe", tags=["subscribe-share"])
api_router.include_router(workflow_share.router, prefix="/workflow", tags=["workflow-share"])
api_router.include_router(site_config.router)
