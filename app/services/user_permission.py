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
        """
        is_admin = cls.is_admin_user(github_user)
        return {
            "github_user": str(github_user or "").strip(),
            "is_admin": is_admin,
            "subscribe_share_manage": is_admin,
            "workflow_share_manage": is_admin,
        }
