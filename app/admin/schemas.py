"""后台请求约束，明确允许修改的账号、配置与业务字段。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdminPayload(BaseModel):
    """拒绝未知字段，避免角色或密码属性被隐式批量赋值。"""

    model_config = ConfigDict(extra="forbid")


class LoginPayload(AdminPayload):
    """登录凭证；用户名在持久化和比对时统一为小写。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CreateUserPayload(AdminPayload):
    """超级管理员创建账号并指定临时密码。"""

    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    role: Literal["admin", "super_admin"] = "admin"


class UpdateUserPayload(AdminPayload):
    """更新账号状态或角色；密码重置使用单独的接口。"""

    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["admin", "super_admin"]
    enabled: bool


class ResetPasswordPayload(AdminPayload):
    """管理员设置的新临时密码。"""

    password: str = Field(min_length=12, max_length=128)


class ChangePasswordPayload(ResetPasswordPayload):
    """本人修改密码时必须验证旧密码。"""

    current_password: str = Field(min_length=1, max_length=128)


class FeaturesPayload(AdminPayload):
    """允许在线修改的公开功能配置，不包含任何部署密钥。"""

    community_medals_enabled: bool
    community_medals_preview_users: list[str] = Field(max_length=200)
    github_admin_users: list[str] = Field(max_length=200)

    @field_validator("community_medals_preview_users", "github_admin_users")
    @classmethod
    def normalize_users(cls, value: list[str]) -> list[str]:
        """规范化 GitHub 名单并限制单项长度，避免保存任意文本。"""
        import re

        normalized = sorted({item.strip().lower() for item in value if item.strip()})
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38})", item) for item in normalized):
            raise ValueError("GitHub 用户名格式无效")
        return normalized


class ConfirmPayload(AdminPayload):
    """危险操作必须提交与页面确认文案一致的文字。"""

    confirm: str = Field(max_length=30)


class CacheDeletePayload(ConfirmPayload):
    """仅允许删除共享识别命名空间内的精确缓存键。"""

    key: str = Field(min_length=1, max_length=4096)


class PushStatePayload(ConfirmPayload):
    """
    运营者明确确认后停用或恢复一个推送实例，不接受密钥字段。
    """

    enabled: bool = Field(description="是否恢复该实例已有凭据的权限")
