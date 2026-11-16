"""
验证持久媒体对应关系的公共 API、季号、冲突和后台撤销。
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import data
from app.admin.schemas import MappingUpdatePayload
from app.api.mapping import router
from app.db.deps import get_db
from app.models import Base
from app.models.admin import AdminAudit
from app.models.mapping import MediaMapping
from app.services.database_schema import ensure_database_schema


def run_scenario(scenario):
    """
    在临时 SQLite 数据库运行真实 HTTP 与持久层合同。
    """
    async def run():
        """
        隔离数据库会话和路由依赖，不访问实际中心服务。
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        await ensure_database_schema(engine, Base, False)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        app = FastAPI()
        app.include_router(router, prefix="/recognize")

        async def session():
            """
            每个 HTTP 请求使用独立的数据库会话。
            """
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await scenario(client, sessions, engine)
        finally:
            await engine.dispose()
    asyncio.run(run())


def payload(**overrides):
    """
    构造来源第 1 季对应 TMDB 第 2 季的共享关系。
    """
    return {"media_source": "anilist", "media_id": "123", "type": "tv", "season": 1,
            "tmdb_id": 456, "tmdb_type": "tv", "tmdb_season": 2, **overrides}


def test_persistent_mapping_conflicts_and_ambiguous_seasons():
    """
    多次上报和冲突不覆盖；季号未知时不能把不同季关系混用。
    """
    async def scenario(client, sessions, engine):
        """
        用独立请求确认写入持久化、唯一约束和明确的未命中行为。
        """
        route = "/recognize/mapping"
        query = {"media_source": "anilist", "media_id": "123", "type": "tv"}
        assert (await client.get(route, params=query)).json()["data"]["item"] is None
        assert (await client.post(route, json=payload())).json()["data"]["item"]["tmdb_season"] == 2
        assert (await client.post(route, json=payload(tmdb_id=999))).json()["data"]["item"]["tmdb_id"] == 456
        response = await client.get(route, params=query)
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["data"]["item"]["tmdb_season"] == 2
        assert (await client.get(route, params={**query, "season": 0})).json()["data"]["item"] is None
        await client.post(route, json=payload(season=0, tmdb_season=0))
        assert (await client.get(route, params=query)).json()["data"]["item"] is None
        assert (await client.get(route, params={**query, "season": 0})).json()["data"]["item"]["tmdb_season"] == 0
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(MediaMapping)) == 2
        async with engine.connect() as connection:
            assert "MEDIA_MAPPING" in await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    run_scenario(scenario)


def test_admin_correction_and_revocation_survive_old_reports(monkeypatch):
    """
    管理员修正和撤销保留原记录并写审计，旧实例不能覆盖或恢复。
    """
    async def scenario(client, sessions, _engine):
        """
        公共写入与后台管理共用同一持久关系表。
        """
        route = "/recognize/mapping"
        await client.post(route, json=payload())
        async with sessions() as db:
            result = await data.list_data(db, "media-mappings", "123", 1, 20, "", True, "tv", "anilist")
            assert result["total"] == 1
            row_id = result["items"][0]["id"]
            corrected = MappingUpdatePayload(confirm="保存", tmdb_id=789, tmdb_type="tv", tmdb_season=0, blocked=False)
            await data.update_mapping(db, row_id, corrected, "test-admin")
        assert (await client.post(route, json=payload())).json()["data"]["item"]["tmdb_id"] == 789
        async with sessions() as db:
            await data.update_mapping(db, row_id, corrected.model_copy(update={"blocked": True}), "test-admin")
        assert (await client.post(route, json=payload())).json()["data"]["item"] is None
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(MediaMapping)) == 1
            assert await db.scalar(select(func.count()).select_from(AdminAudit)) == 2
    async def invalidate():
        """
        管理缓存广播使用受控替身，避免测试访问 Redis。
        """
    monkeypatch.setattr(data, "admin_cache_invalidation", SimpleNamespace(invalidate=invalidate))
    run_scenario(scenario)


@pytest.mark.parametrize("override", [
    {"media_source": "themoviedb"}, {"media_source": "bad source"}, {"media_id": "0"},
    {"media_id": " "}, {"tmdb_id": 0}, {"tmdb_season": -1}, {"type": "music"},
    {"tmdb_type": "movie", "tmdb_season": 2},
])
def test_invalid_or_reverse_reports_are_rejected(override):
    """
    无效身份、反向关系、音乐和电影季号在公开边界被拒绝。
    """
    async def scenario(client, _sessions, _engine):
        """
        真实 HTTP 验证失败不能形成记录。
        """
        assert (await client.post("/recognize/mapping", json=payload(**override))).status_code == 422
    run_scenario(scenario)
