"""
用户权限相关API路由。
"""
from typing import Optional

from fastapi import APIRouter

from app.services.user_permission import UserPermissionService

router = APIRouter()


@router.get("/permissions")
async def user_permissions(github_user: Optional[str] = None):
    """
    查询当前 GitHub 用户在服务端配置中的权限。
    """
    return UserPermissionService.get_permissions(github_user)


@router.get("/features")
async def user_features():
    """
    查询对所有实例相同的全局功能开关，不接收任何用户参数。
    """
    return UserPermissionService.get_features()
