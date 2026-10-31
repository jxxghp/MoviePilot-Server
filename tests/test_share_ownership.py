"""工作流与订阅分享的上下文过滤和私有删除令牌测试。"""

import asyncio
import hashlib

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.subscribe_share import router as subscribe_router
from app.api.workflow_share import router as workflow_router
from app.core.cache import cache_manager
from app.db.deps import get_db
from app.models import Base, SubscribeShare, WorkflowShare


def _run_with_client(scenario) -> None:
    """在隔离内存数据库中挂载分享路由并执行场景。"""

    async def run() -> None:
        cache_manager.workflow_share_cache.clear()
        cache_manager.share_cache.clear()
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def override_get_db():
            """为路由提供隔离数据库会话。"""
            async with session_factory() as session:
                yield session

        app = FastAPI()
        app.include_router(workflow_router, prefix="/workflow")
        app.include_router(subscribe_router, prefix="/subscribe")
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await scenario(client, session_factory)
        await engine.dispose()

    asyncio.run(run())


def _workflow_payload(**overrides) -> dict:
    """构造最小工作流分享请求。"""
    payload = {
        "share_title": "整理工作流",
        "share_user": "alice",
        "share_uid": "uid-alice",
        "name": "整理",
        "actions": "[]",
        "flows": "[]",
    }
    payload.update(overrides)
    return payload


def _assert_envelope(payload: dict, success: bool) -> None:
    """分享写接口必须始终返回固定的三字段信封。"""
    assert set(payload) == {"success", "message", "data"}
    assert payload["success"] is success


def test_workflow_share_creation_returns_private_owner_token() -> None:
    """创建分享只返回一次私有令牌，存储和公开列表都不能泄露它。"""

    async def scenario(client, session_factory) -> None:
        response = await client.post(
            "/workflow/share",
            json=_workflow_payload(context='{"content": "bGVnYWN5"}'),
        )
        payload = response.json()
        _assert_envelope(payload, True)
        assert set(payload["data"]) == {"id", "owner_token"}
        owner_token = payload["data"]["owner_token"]
        share_id = payload["data"]["id"]
        assert isinstance(owner_token, str) and len(owner_token) >= 40

        async with session_factory() as session:
            stored = await session.get(WorkflowShare, share_id)
        assert stored is not None
        assert stored.context is None
        expected_hash = hashlib.sha256(owner_token.encode()).hexdigest()
        assert stored.owner_token_hash == expected_hash
        assert len(stored.owner_token_hash) == 64
        assert int(stored.owner_token_hash, 16) >= 0
        assert "owner_token_hash" not in stored.dict()
        assert "owner_token" not in stored.dict()

        listed = (await client.get("/workflow/shares")).json()
        assert len(listed) == 1
        assert listed[0]["share_uid"] == "uid-alice"
        assert listed[0]["context"] is None
        assert "owner_token_hash" not in listed[0]
        assert "owner_token" not in listed[0]

        duplicate = await client.post("/workflow/share", json=_workflow_payload())
        _assert_envelope(duplicate.json(), False)
        assert duplicate.json()["data"] is None

    _run_with_client(scenario)


def test_subscribe_share_creation_returns_private_owner_token() -> None:
    """订阅分享采用同样的令牌存储与公开字段过滤规则。"""

    async def scenario(client, session_factory) -> None:
        body = {
            "share_title": "测试订阅",
            "share_user": "alice",
            "share_uid": "uid-alice",
            "name": "测试剧集",
            "type": "电视剧",
            "tmdbid": 550,
        }
        response = await client.post("/subscribe/share", json=body)
        payload = response.json()
        _assert_envelope(payload, True)
        assert set(payload["data"]) == {"id", "owner_token"}
        owner_token = payload["data"]["owner_token"]
        share_id = payload["data"]["id"]

        async with session_factory() as session:
            stored = await session.get(SubscribeShare, share_id)
        assert stored is not None
        assert stored.owner_token_hash == hashlib.sha256(owner_token.encode()).hexdigest()
        assert len(stored.owner_token_hash) == 64

        listed = (await client.get("/subscribe/shares")).json()
        assert len(listed) == 1
        assert listed[0]["share_uid"] == "uid-alice"
        assert "owner_token_hash" not in listed[0]
        assert "owner_token" not in listed[0]

        duplicate = await client.post("/subscribe/share", json=body)
        _assert_envelope(duplicate.json(), False)
        assert duplicate.json()["data"] is None

    _run_with_client(scenario)


def test_share_deletion_requires_private_owner_token() -> None:
    """公开 share_uid 不能删除分享，Header 中的正确令牌才能删除。"""

    async def scenario(client, session_factory) -> None:
        workflow = (
            await client.post("/workflow/share", json=_workflow_payload())
        ).json()["data"]
        subscribe = (
            await client.post("/subscribe/share", json={
                "share_title": "删除测试",
                "share_user": "bob",
                "share_uid": "uid-bob",
                "name": "删除测试剧集",
                "type": "电视剧",
                "tmdbid": 551,
            })
        ).json()["data"]

        cases = (
            (WorkflowShare, "/workflow", workflow),
            (SubscribeShare, "/subscribe", subscribe),
        )
        for model, prefix, created in cases:
            share_id = created["id"]
            owner_token = created["owner_token"]

            missing = await client.delete(
                f"{prefix}/share/{share_id}",
                params={"share_uid": "uid-alice"},
            )
            _assert_envelope(missing.json(), False)
            assert missing.json()["data"] is None
            async with session_factory() as session:
                assert await session.get(model, share_id) is not None

            wrong = await client.delete(
                f"{prefix}/share/{share_id}",
                headers={"X-Share-Owner-Token": "wrong-token"},
            )
            _assert_envelope(wrong.json(), False)
            async with session_factory() as session:
                assert await session.get(model, share_id) is not None

            accepted = await client.delete(
                f"{prefix}/share/{share_id}",
                headers={"X-Share-Owner-Token": owner_token},
            )
            _assert_envelope(accepted.json(), True)
            assert accepted.json()["data"] is None
            async with session_factory() as session:
                assert await session.get(model, share_id) is None

    _run_with_client(scenario)


def test_legacy_share_without_token_hash_cannot_be_deleted_by_users() -> None:
    """迁移前的历史分享缺少摘要时，普通用户不能凭公开字段删除。"""

    async def scenario(client, session_factory) -> None:
        async with session_factory() as session:
            workflow = WorkflowShare(
                **_workflow_payload(),
                date="2026-10-01 00:00:00",
                count=0,
            )
            subscribe = SubscribeShare(
                share_title="历史订阅",
                share_user="alice",
                share_uid="uid-alice",
                name="历史剧集",
                type="电视剧",
            )
            session.add_all([workflow, subscribe])
            await session.commit()
            workflow_id = workflow.id
            subscribe_id = subscribe.id

        for model, prefix, share_id in (
            (WorkflowShare, "/workflow", workflow_id),
            (SubscribeShare, "/subscribe", subscribe_id),
        ):
            rejected = await client.delete(
                f"{prefix}/share/{share_id}",
                params={"share_uid": "uid-alice"},
                headers={"X-Share-Owner-Token": "guessed-token"},
            )
            _assert_envelope(rejected.json(), False)
            assert rejected.json()["data"] is None
            async with session_factory() as session:
                assert await session.get(model, share_id) is not None

    _run_with_client(scenario)
