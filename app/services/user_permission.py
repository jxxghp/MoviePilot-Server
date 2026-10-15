"""
用户权限服务。
"""
from typing import Any, Dict, Optional

from app.core.config import settings


class UserPermissionService:
    """
    用户权限服务。
    """

    @staticmethod
    def _normalize_github_user(github_user: Optional[str]) -> str:
        """
        规范化 GitHub 用户名，便于和环境变量配置比较。
        """
        return str(github_user or "").strip().lower()

    @classmethod
    def is_admin_user(cls, github_user: Optional[str]) -> bool:
        """
        判断 GitHub 用户是否为服务端配置的管理员。
        """
        normalized_user = cls._normalize_github_user(github_user)
        return bool(normalized_user and normalized_user in settings.admin_users)

    @classmethod
    def get_permissions(cls, github_user: Optional[str]) -> Dict[str, Any]:
        """
        获取用户在官方服务端上的功能权限。

        community_medals_preview：该用户是否在 COMMUNITY_MEDALS_PREVIEW_USERS 预览名单中，
        供维护者在全局开关关闭时先行验证；名单为空时任何人（包括管理员）都不可预览。
        """
        is_admin = cls.is_admin_user(github_user)
        normalized_user = cls._normalize_github_user(github_user)
        return {
            "github_user": str(github_user or "").strip(),
            "is_admin": is_admin,
            "subscribe_share_manage": is_admin,
            "workflow_share_manage": is_admin,
            "community_medals_preview": bool(
                normalized_user and normalized_user in settings.community_medals_preview_users
            ),
        }

    @staticmethod
    def get_features() -> Dict[str, Any]:
        """
        获取对所有实例相同的全局功能开关。

        响应与请求方无关，可由反向代理短时缓存；新增字段须保持这一性质。
        community_medals：社区勋章是否对所有实例开放（COMMUNITY_MEDALS_ENABLED）。
        """
        return {"community_medals": settings.COMMUNITY_MEDALS_ENABLED}
