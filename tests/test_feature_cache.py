"""验证公共配置缓存的并发合并、失效和独立响应，防止权限配置长期陈旧。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from cacheout import Cache

from app.admin import config, invalidation
from app.admin.schemas import FeaturesPayload
from app.core.cache import cache_manager
from app.core.config import settings


class FeatureDatabase:
    """统计独立请求实际访问配置数据库的次数。"""

    def __init__(self, payload: FeaturesPayload | None) -> None:
        """保存可替换的数据库行，并从零记录读取次数。"""
        self.row = SimpleNamespace(value=payload.model_dump_json()) if payload else None
        self.calls = 0

    async def get(self, model, key, *, populate_existing=False):
        """让出事件循环，模拟 SQL 读取时其他公共请求同时到达。"""
        self.calls += 1
        assert populate_existing
        await asyncio.sleep(0)
        return self.row


@pytest.fixture(autouse=True)
def isolated_features_cache(monkeypatch):
    """每个场景使用独立缓存、事件循环锁及可推进的过期时钟。"""
    clock = [1000.0]

    def now():
        """读取测试控制的单调递增时间，不等待真实 TTL。"""
        return clock[0]

    monkeypatch.setattr(cache_manager, "features_cache", Cache(maxsize=1, ttl=3, timer=now))
    monkeypatch.setattr(config, "_features_lock", asyncio.Lock())
    return clock


def test_concurrent_public_reads_share_one_query_and_return_independent_values():
    """不同请求的会话合并为一次配置读取，响应列表不能互相污染。"""
    async def run():
        """并发请求冷缓存，并修改其中一份返回值验证后续请求。"""
        payload = FeaturesPayload(community_medals_enabled=True, community_medals_preview_users=[], github_admin_users=["alice"])
        databases = [FeatureDatabase(payload) for _ in range(32)]
        values = await asyncio.gather(*(config.read_cached_features(db) for db in databases))
        assert sum(db.calls for db in databases) == 1
        assert all(value == payload.model_dump() for value in values)
        values[0]["github_admin_users"].append("intruder")
        assert (await config.read_cached_features(databases[0]))["github_admin_users"] == ["alice"]
        assert values[1]["github_admin_users"] == ["alice"]
    asyncio.run(run())


def test_expiration_refreshes_public_values_but_admin_reads_are_always_fresh(isolated_features_cache):
    """公共读取最多保留三秒，后台读取不复用公共缓存。"""
    async def run():
        """替换数据库行，分别验证后台即时读取和公共缓存过期。"""
        db = FeatureDatabase(FeaturesPayload(community_medals_enabled=False, community_medals_preview_users=[], github_admin_users=[]))
        assert not (await config.read_cached_features(db))["community_medals_enabled"]
        db.row = SimpleNamespace(value=FeaturesPayload(community_medals_enabled=True, community_medals_preview_users=[], github_admin_users=[]).model_dump_json())
        assert (await config.read_features(db))["community_medals_enabled"]
        assert not (await config.read_cached_features(db))["community_medals_enabled"]
        isolated_features_cache[0] += 3.01
        assert (await config.read_cached_features(db))["community_medals_enabled"]
        assert db.calls == 3
    asyncio.run(run())


def test_missing_online_configuration_is_cached_with_environment_fallback(monkeypatch):
    """数据库没有配置时也合并查询，同时保持环境变量回退语义。"""
    async def run():
        """改变测试环境开关，不让缓存把缺失行误当作持续未命中。"""
        db = FeatureDatabase(None)
        monkeypatch.setattr(settings, "COMMUNITY_MEDALS_ENABLED", False)
        assert not (await config.read_cached_features(db))["community_medals_enabled"]
        monkeypatch.setattr(settings, "COMMUNITY_MEDALS_ENABLED", True)
        assert (await config.read_cached_features(db))["community_medals_enabled"]
        assert db.calls == 1
    asyncio.run(run())


def test_database_failure_does_not_poison_feature_cache():
    """查询故障仍向调用者报告，恢复后能够正常填充缓存。"""
    async def run():
        """让第一次数据库访问失败，再验证下一次读取。"""
        db = FeatureDatabase(FeaturesPayload(community_medals_enabled=True, community_medals_preview_users=[], github_admin_users=[]))
        db.get = AsyncMock(side_effect=[RuntimeError("database unavailable"), db.row])
        with pytest.raises(RuntimeError, match="database unavailable"):
            await config.read_cached_features(db)
        assert (await config.read_cached_features(db))["community_medals_enabled"]
        assert db.get.await_count == 2
    asyncio.run(run())


def test_invalidation_during_read_cannot_repopulate_old_configuration(monkeypatch):
    """失效前开始的数据库读取可以完成，但不能给后续请求重新缓存旧权限。"""
    async def run():
        """在旧配置读取尚未返回时发布失效，然后检查下一个请求。"""
        entered, release = asyncio.Event(), asyncio.Event()
        old = FeaturesPayload(community_medals_enabled=False, community_medals_preview_users=[], github_admin_users=["alice"])
        new = FeaturesPayload(community_medals_enabled=False, community_medals_preview_users=[], github_admin_users=[])
        calls = 0

        async def delayed_read(db):
            """首个查询持有旧快照，后续查询读取已经更新的配置。"""
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                return old
            return new

        monkeypatch.setattr(config, "_read_stored_features", delayed_read)
        pending = asyncio.create_task(config.read_cached_features(None))
        await entered.wait()
        cache_manager.clear_all()
        release.set()
        assert (await pending)["github_admin_users"] == ["alice"]
        assert (await config.read_cached_features(None))["github_admin_users"] == []
        assert calls == 2
    asyncio.run(run())


def test_revision_watcher_invalidates_another_worker_feature_cache(monkeypatch):
    """其他 worker 发布修订号后，已有的本地功能配置缓存也会失效。"""
    async def run():
        """模拟另一个进程保存配置，使用真实轮询任务同步修订号。"""
        db = FeatureDatabase(FeaturesPayload(community_medals_enabled=False, community_medals_preview_users=[], github_admin_users=["alice"]))
        await config.read_cached_features(db)
        db.row = SimpleNamespace(value=FeaturesPayload(community_medals_enabled=False, community_medals_preview_users=[], github_admin_users=[]).model_dump_json())
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        watcher = invalidation.AdminCacheInvalidation()
        watcher.revision = "1"
        await redis.set(watcher.key(), "2")

        def isolated_redis():
            """提供其他进程已经更新的修订号存储。"""
            return redis

        monkeypatch.setattr(invalidation, "get_redis", isolated_redis)
        try:
            await watcher.start()
            async with asyncio.timeout(1):
                while watcher.revision != "2":
                    await asyncio.sleep(0.001)
            assert (await config.read_cached_features(db))["github_admin_users"] == []
            assert db.calls == 2
        finally:
            await watcher.stop()
            await redis.aclose()
    asyncio.run(run())
