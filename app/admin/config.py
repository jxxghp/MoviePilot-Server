"""后台功能配置，数据库覆盖项跨进程生效并保留环境变量回退。"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.admin.schemas import FeaturesPayload
from app.core.config import settings
from app.models.admin import AdminSetting, AdminUser

FEATURES_KEY = "public_features"


async def read_features(db: AsyncSession) -> dict:
    """配置尚未在线保存时，严格沿用现有环境变量含义。"""
    row = await db.get(AdminSetting, FEATURES_KEY)
    if row:
        return FeaturesPayload.model_validate_json(row.value).model_dump()
    return {
        "community_medals_enabled": settings.COMMUNITY_MEDALS_ENABLED,
        "community_medals_preview_users": sorted(settings.community_medals_preview_users),
        "github_admin_users": sorted(settings.admin_users),
    }


async def save_features(db: AsyncSession, payload: FeaturesPayload, actor: AdminUser) -> dict:
    """功能配置和变更前后值在一个事务内保存；不修改部署环境和密钥。"""
    before = await read_features(db)
    after = payload.model_dump()
    await db.merge(AdminSetting(key=FEATURES_KEY, value=json.dumps(after)))
    record(db, actor, "features.update", "public_features", {"before": before, "after": after})
    await db.commit()
    return after
