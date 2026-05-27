"""
API路由汇总
"""
from fastapi import APIRouter

from app.api import (
    media_recognize_share,
    plugin_statistic,
    subscribe_statistic,
    subscribe_share,
    usage_statistic,
    user,
    workflow_share,
    u115_auth,
)

api_router = APIRouter()

# 包含各个模块的路由
api_router.include_router(plugin_statistic.router, prefix="/plugin", tags=["plugin-statistic"])
api_router.include_router(subscribe_statistic.router, prefix="/subscribe", tags=["subscribe-statistic"])
api_router.include_router(subscribe_share.router, prefix="/subscribe", tags=["subscribe-share"])
api_router.include_router(workflow_share.router, prefix="/workflow", tags=["workflow-share"])
api_router.include_router(media_recognize_share.router, prefix="/recognize", tags=["media-recognize-share"])
api_router.include_router(u115_auth.router, prefix="/u115", tags=["u115-auth"])
api_router.include_router(usage_statistic.router, prefix="/usage", tags=["usage-statistic"])
api_router.include_router(user.router, prefix="/user", tags=["user"])
