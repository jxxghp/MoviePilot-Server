"""工作流与订阅分享的上下文过滤和删除归属测试。"""

import asyncio

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


def test_workflow_share_drops_submitted_context() -> None:
    """发布工作流分享时不得保存请求中的执行上下文。"""

    async def scenario(client, session_factory) -> None:
        response = await client.post(
            "/workflow/share",
            json=_workflow_payload(context='{"content": "bGVnYWN5"}'),
        )
        assert response.json()["code"] == 0

        async with session_factory() as session:
            stored = (await session.execute(select(WorkflowShare))).scalar_one()
        assert stored.context is None

    _run_with_client(scenario)


def test_workflow_shares_never_return_stored_context() -> None:
    """历史数据中残留的执行上下文不得下发给复用方。"""

    async def scenario(client, session_factory) -> None:
        async with session_factory() as session:
            session.add(WorkflowShare(
                **_workflow_payload(),
                context='{"content": "bGVnYWN5"}',
                date="2026-10-01 00:00:00",
                count=0,
            ))
            await session.commit()

        response = await client.get("/workflow/shares")

        shares = response.json()
        assert len(shares) == 1
        assert shares[0]["context"] is None
        assert shares[0]["actions"] == "[]"

    _run_with_client(scenario)


def test_workflow_share_delete_requires_owner_uid() -> None:
    """只有分享者本人的实例 ID 可以删除工作流分享。"""

    async def scenario(client, session_factory) -> None:
        await client.post("/workflow/share", json=_workflow_payload())
        async with session_factory() as session:
            share_id = (await session.execute(select(WorkflowShare.id))).scalar_one()

        rejected = await client.delete(f"/workflow/share/{share_id}", params={"share_uid": "uid-mallory"})
        assert rejected.json()["code"] == 1
        async with session_factory() as session:
            assert (await session.execute(select(WorkflowShare.id))).scalar_one_or_none() == share_id

        accepted = await client.delete(f"/workflow/share/{share_id}", params={"share_uid": "uid-alice"})
        assert accepted.json()["code"] == 0
        async with session_factory() as session:
            assert (await session.execute(select(WorkflowShare.id))).scalar_one_or_none() is None

    _run_with_client(scenario)


def test_subscribe_share_delete_requires_owner_uid() -> None:
    """只有分享者本人的实例 ID 可以删除订阅分享。"""

    async def scenario(client, session_factory) -> None:
        async with session_factory() as session:
            share = SubscribeShare(
                share_title="测试订阅",
                share_user="alice",
                share_uid="uid-alice",
                name="测试剧集",
                type="电视剧",
            )
            session.add(share)
            await session.commit()
            share_id = share.id

        rejected = await client.delete(f"/subscribe/share/{share_id}", params={"share_uid": "uid-mallory"})
        assert rejected.json()["code"] == 1
        async with session_factory() as session:
            assert (await session.execute(select(SubscribeShare.id))).scalar_one_or_none() == share_id

        accepted = await client.delete(f"/subscribe/share/{share_id}", params={"share_uid": "uid-alice"})
        assert accepted.json()["code"] == 0
        async with session_factory() as session:
            assert (await session.execute(select(SubscribeShare.id))).scalar_one_or_none() is None

    _run_with_client(scenario)
