"""
用户权限相关API路由。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.user_permission import UserPermissionService
from app.admin.config import read_features
from app.db.deps import get_db

router = APIRouter()


@router.get("/permissions")
async def user_permissions(github_user: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """
    查询当前 GitHub 用户在服务端配置中的权限。
    """
    features = await read_features(db)
    result = UserPermissionService.get_permissions(github_user)
    username = str(github_user or "").strip().lower()
    is_admin = bool(username and username in features["github_admin_users"])
    result.update(is_admin=is_admin, subscribe_share_manage=is_admin, workflow_share_manage=is_admin,
                  community_medals_preview=bool(username and username in features["community_medals_preview_users"]))
    return result


@router.get("/features")
async def user_features(db: AsyncSession = Depends(get_db)):
    """
    查询对所有实例相同的全局功能开关，不接收任何用户参数。
    """
    return {"community_medals": (await read_features(db))["community_medals_enabled"]}
