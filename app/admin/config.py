"""后台功能配置，数据库覆盖项跨进程生效并保留环境变量回退。"""

import asyncio
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.admin.invalidation import admin_cache_invalidation
from app.admin.schemas import FeaturesPayload
from app.core.cache import cache_manager
from app.core.config import settings
from app.models.admin import AdminSetting, AdminUser

FEATURES_KEY = "public_features"
_CACHE_MISS = object()
_features_lock = asyncio.Lock()


async def _read_stored_features(db: AsyncSession) -> FeaturesPayload | None:
    """重新读取持久化配置，避免复用同一会话中的旧 ORM 实例。"""
    row = await db.get(AdminSetting, FEATURES_KEY, populate_existing=True)
    return FeaturesPayload.model_validate_json(row.value) if row else None


def _effective_features(payload: FeaturesPayload | None) -> dict:
    """返回独立配置副本，缺失数据库配置时继续使用环境变量。"""
    if payload is not None:
        values = payload.model_dump()
        if "central_knowledge_enabled" not in payload.model_fields_set:
            values["central_knowledge_enabled"] = settings.KNOWLEDGE_ENABLED
        return values
    return {
        "community_medals_enabled": settings.COMMUNITY_MEDALS_ENABLED,
        "central_knowledge_enabled": settings.KNOWLEDGE_ENABLED,
        "community_medals_preview_users": sorted(settings.community_medals_preview_users),
        "github_admin_users": sorted(settings.admin_users),
    }


async def read_features(db: AsyncSession) -> dict:
    """后台读取和审计始终以数据库当前值为准。"""
    return _effective_features(await _read_stored_features(db))


async def read_cached_features(db: AsyncSession) -> dict:
    """合并公共接口的并发配置读取，短缓存及修订号共同限制陈旧时间。"""
    payload = cache_manager.features_cache.get(FEATURES_KEY, _CACHE_MISS)
    if payload is _CACHE_MISS:
        async with _features_lock:
            payload = cache_manager.features_cache.get(FEATURES_KEY, _CACHE_MISS)
            if payload is _CACHE_MISS:
                generation = cache_manager.features_generation
                payload = await _read_stored_features(db)
                if generation == cache_manager.features_generation:
                    cache_manager.features_cache.set(FEATURES_KEY, payload)
    return _effective_features(payload)


async def knowledge_enabled(db: AsyncSession) -> bool:
    """读取后台知识总开关；未保存过在线配置时沿用部署环境设置。"""
    return bool((await read_cached_features(db))["central_knowledge_enabled"])


async def save_features(db: AsyncSession, payload: FeaturesPayload, actor: AdminUser) -> dict:
    """功能配置和变更前后值在一个事务内保存；不修改部署环境和密钥。"""
    before = await read_features(db)
    after = payload.model_dump()
    await db.merge(AdminSetting(key=FEATURES_KEY, value=json.dumps(after)))
    record(db, actor, "features.update", "public_features", {"before": before, "after": after})
    await db.commit()
    await admin_cache_invalidation.invalidate()
    return after
