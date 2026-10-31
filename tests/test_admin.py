"""后台认证、权限、用户生命周期与真实数据管理的隔离回归测试。"""

import asyncio
import json
from decimal import Decimal

import fakeredis.aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.api import router
from app.admin.auth import COOKIE_NAME, session_key
from app.admin.schemas import CreateUserPayload
from app.admin.static import install_admin_pages
from app.admin.users import create_user
from app.api.user import router as public_user_router
from app.core.cache import cache_manager
from app.core.config import settings
from app.db import redis as redis_module
from app.db.deps import get_db
from app.models import Base, PluginRating, PluginRatingSummary, PluginStatistics, SubscribeShare, SubscribeStatistics, WorkflowShare
from app.models.admin import AdminAudit, AdminSetting, AdminUser

PASSWORD = "root-initial-password-123"


def run_scenario(monkeypatch, scenario):
    """每个场景使用独立 SQL 数据库和 Redis，保留真实认证与授权依赖。"""
    async def run():
        """创建可通过 ASGI 访问的隔离后台。"""
        cache_manager.clear_all()
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "ADMIN_COOKIE_SECURE", False)
        monkeypatch.setattr(settings, "ADMIN_LOGIN_ATTEMPTS", 100)
        async with factory() as db:
            db.add(AdminSetting(key="__user_lock", value="1"))
            await db.flush()
            root = await create_user(db, CreateUserPayload(username="root", display_name="Root", password=PASSWORD, role="super_admin"))
            root.must_change_password = False
            await db.commit()

        async def isolated_db():
            """通过标准依赖覆盖隔离数据库，不跳过任何认证逻辑。"""
            async with factory() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        app.include_router(public_user_router, prefix="/user")
        install_admin_pages(app)
        app.dependency_overrides[get_db] = isolated_db
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await scenario(client, factory, redis)
        finally:
            await redis.aclose()
            await engine.dispose()
    asyncio.run(run())


async def login(client, username="root", password=PASSWORD):
    """用真实登录接口获取 CSRF 标识，Cookie 由 HTTP 客户端保存。"""
    response = await client.post("/admin/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_protected_routes_and_csrf(monkeypatch):
    """全部数据入口需要认证，已登录的写请求仍必须携带 CSRF 标识。"""
    async def scenario(client, factory, redis):
        """覆盖匿名、伪造会话和跨站写入。"""
        for path in ("overview", "users", "features", "system", "recognition", "data/ratings", "data/audit", "maintenance/preview"):
            assert (await client.get(f"/admin/api/{path}")).status_code == 401
        response = await client.post("/admin/api/auth/login", json={"username": "root", "password": "wrong"})
        assert response.status_code == 401
        csrf = await login(client)
        assert (await client.post("/admin/api/auth/logout")).status_code == 403
        assert (await client.post("/admin/api/auth/logout", headers=csrf)).status_code == 200
        assert (await client.get("/admin/api/auth/me")).status_code == 401
        assert (await client.get("/admin/api/missing")).status_code == 404
        assert (await client.get("/admin/api/overview")).headers["cache-control"] == "no-store"
    run_scenario(monkeypatch, scenario)


def test_multiuser_password_and_role_boundaries(monkeypatch):
    """超级管理员创建用户；临时密码限制、首次改密和普通管理员边界均生效。"""
    async def scenario(client, factory, redis):
        """完成创建、首次登录、修改密码和权限拒绝的完整流程。"""
        csrf = await login(client)
        response = await client.post("/admin/api/users", headers=csrf, json={
            "username": "operator", "display_name": "运营", "password": "temporary-password-123", "role": "admin",
        })
        assert response.status_code == 200
        assert "password_hash" not in response.json()
        csrf = await login(client, "operator", "temporary-password-123")
        assert (await client.get("/admin/api/overview")).status_code == 403
        response = await client.post("/admin/api/auth/password", headers=csrf, json={
            "current_password": "temporary-password-123", "password": "new-operator-password-456",
        })
        assert response.status_code == 200
        assert (await client.get("/admin/api/auth/me")).status_code == 401
        csrf = await login(client, "operator", "new-operator-password-456")
        assert (await client.get("/admin/api/overview")).status_code == 200
        assert (await client.get("/admin/api/users")).status_code == 403
        assert (await client.get("/admin/api/maintenance/preview")).status_code == 403
        features = (await client.get("/admin/api/features")).json()
        assert (await client.put("/admin/api/features", json=features, headers=csrf)).status_code == 403
        async with factory() as db:
            audit = (await db.scalars(select(AdminAudit))).all()
            assert all("password-" not in item.detail for item in audit)
    run_scenario(monkeypatch, scenario)


def test_user_changes_revoke_sessions_and_last_superadmin(monkeypatch):
    """不能禁用最后一个超级管理员，账号重置和禁用会撤销旧会话。"""
    async def scenario(client, factory, redis):
        """模拟不同用户的既有会话，检查数据库版本失效边界。"""
        csrf = await login(client)
        root = (await client.get("/admin/api/auth/me")).json()["user"]
        response = await client.patch(f'/admin/api/users/{root["id"]}', headers=csrf, json={"display_name": "Root", "role": "admin", "enabled": True})
        assert response.status_code == 409
        response = await client.post("/admin/api/users", headers=csrf, json={"username": "staff", "display_name": "Staff", "password": "staff-temporary-123"})
        uid = response.json()["id"]
        await login(client, "staff", "staff-temporary-123")
        staff_token = client.cookies.get(COOKIE_NAME)
        # 新客户端代表另一台设备，避免登录旋转当前设备会话干扰撤销验证。
        client.cookies.clear()
        csrf = await login(client)
        assert (await client.post(f"/admin/api/users/{uid}/password", headers=csrf, json={"password": "staff-reset-password-456"})).status_code == 200
        saved = client.cookies.get(COOKIE_NAME)
        client.cookies.set(COOKIE_NAME, staff_token, path="/admin", domain="test.local")
        assert (await client.get("/admin/api/auth/me")).status_code == 401
        client.cookies.set(COOKIE_NAME, saved, path="/admin", domain="test.local")
        assert (await client.patch(f"/admin/api/users/{uid}", headers=csrf, json={"display_name": "Staff", "role": "admin", "enabled": False})).status_code == 200
        response = await client.post("/admin/api/auth/login", json={"username": "staff", "password": "staff-reset-password-456"})
        assert response.status_code == 401
    run_scenario(monkeypatch, scenario)


def test_online_features_reach_public_contract(monkeypatch):
    """在线配置覆盖环境变量，管理员与勋章预览名单仍保持独立。"""
    async def scenario(client, factory, redis):
        """通过后台保存，再从公开接口回读实际生效值。"""
        csrf = await login(client)
        payload = {"community_medals_enabled": True, "community_medals_preview_users": [" Alice "], "github_admin_users": ["Bob"]}
        assert (await client.put("/admin/api/features", headers=csrf, json=payload)).status_code == 200
        assert (await client.get("/user/features")).json() == {"community_medals": True}
        alice = (await client.get("/user/permissions", params={"github_user": "ALICE"})).json()
        bob = (await client.get("/user/permissions", params={"github_user": "Bob"})).json()
        assert alice["community_medals_preview"] and not alice["is_admin"]
        assert bob["is_admin"] and not bob["community_medals_preview"]
    run_scenario(monkeypatch, scenario)


def test_business_details_delete_and_rating_summary(monkeypatch):
    """详情无计数副作用；删除评分必须重算汇总，操作记录不能被删除。"""
    async def scenario(client, factory, redis):
        """使用实际 SQL 数据验证删除事务及隐私字段过滤。"""
        async with factory() as db:
            share = WorkflowShare(
                name="流程",
                share_title="流程分享",
                share_user="Alice",
                share_uid="uid-private",
                owner_token_hash="a" * 64,
                context="secret",
                count=4,
            )
            subscribe_share = SubscribeShare(
                name="订阅流程",
                share_title="订阅分享",
                share_user="Alice",
                share_uid="uid-private",
                owner_token_hash="b" * 64,
                type="电视剧",
            )
            rating = PluginRating(plugin_id="plugin", user_uid="private-installation", rating=Decimal("5.0"), created_at="2026-10-19", updated_at="2026-10-19")
            db.add_all([share, subscribe_share, rating, PluginRating(plugin_id="plugin", user_uid="other-installation", rating=Decimal("3.0"), created_at="2026-10-19", updated_at="2026-10-19"), PluginRatingSummary(plugin_id="plugin", average_rating=4, rating_count=2, updated_at="2026-10-19")])
            await db.commit()
            sid, subscribe_sid, rid = share.id, subscribe_share.id, rating.id
        csrf = await login(client)
        detail = (await client.get(f"/admin/api/data/workflow-shares/{sid}")).json()
        assert detail["count"] == 4 and "context" not in detail and detail["share_uid"] != "uid-private"
        subscribe_detail = (await client.get(f"/admin/api/data/subscribe-shares/{subscribe_sid}")).json()
        overview = (await client.get("/admin/api/overview")).json()
        workflow_list = (await client.get("/admin/api/data/workflow-shares")).json()
        subscribe_list = (await client.get("/admin/api/data/subscribe-shares")).json()
        serialized_responses = [detail, subscribe_detail, overview, workflow_list, subscribe_list]
        assert all("owner_token_hash" not in json.dumps(response) for response in serialized_responses)
        assert all("owner_token" not in json.dumps(response) for response in serialized_responses)
        assert (await client.get("/admin/api/data/workflow-shares", params={"sort": "owner_token_hash"})).status_code == 422
        assert (await client.get("/admin/api/data/subscribe-shares", params={"sort": "owner_token_hash"})).status_code == 422
        assert (await client.get("/admin/api/data/ratings", params={"sort": "password_hash"})).status_code == 422
        assert (await client.request("DELETE", f"/admin/api/data/ratings/{rid}", headers=csrf, json={"confirm": ""})).status_code == 422
        response = await client.request("DELETE", f"/admin/api/data/ratings/{rid}", headers=csrf, json={"confirm": "删除"})
        assert response.status_code == 200
        async with factory() as db:
            summary = await db.scalar(select(PluginRatingSummary))
            assert summary.rating_count == 1 and summary.average_rating == 3
        assert (await client.request("DELETE", "/admin/api/data/audit/1", headers=csrf, json={"confirm": "删除"})).status_code == 403
    run_scenario(monkeypatch, scenario)


def test_recognition_namespace_and_cursor(monkeypatch):
    """扫描和删除仅能操作共享识别记录，不能删除授权会话或其他 Redis 键。"""
    async def scenario(client, factory, redis):
        """确认命名空间、游标及定向删除的实际结果。"""
        prefix = settings.media_recognize_share_redis_prefix + ":item:"
        await redis.set(prefix + "movie|movie||", json.dumps({"keyword": "movie", "name": "电影"}))
        await redis.set("private:token", "secret")
        csrf = await login(client)
        response = await client.get("/admin/api/recognition")
        assert len(response.json()["items"]) == 1
        response = await client.request("DELETE", "/admin/api/recognition", headers=csrf, json={"key": "private:token", "confirm": "删除"})
        assert response.status_code == 422 and await redis.get("private:token") == "secret"
        response = await client.request("DELETE", "/admin/api/recognition", headers=csrf, json={"key": prefix + "movie|movie||", "confirm": "删除"})
        assert response.status_code == 200 and not await redis.exists(prefix + "movie|movie||")
    run_scenario(monkeypatch, scenario)


def test_login_rate_limit(monkeypatch):
    """错误密码与不存在账号共同受共享限流保护。"""
    async def scenario(client, factory, redis):
        """超过窗口限制后返回 429 和明确重试时间。"""
        monkeypatch.setattr(settings, "ADMIN_LOGIN_ATTEMPTS", 2)
        for _ in range(2):
            assert (await client.post("/admin/api/auth/login", json={"username": "missing", "password": "wrong"})).status_code == 401
        response = await client.post("/admin/api/auth/login", json={"username": "missing", "password": "wrong"})
        assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    run_scenario(monkeypatch, scenario)


def test_cleanup_preview_execution_and_audit(monkeypatch):
    """清理预览与执行使用同一规则，保留范围外记录并记录实际影响数量。"""
    async def scenario(client, factory, redis):
        """在真实临时数据库执行清理，通过 API 回读结果与审计。"""
        from app.services import data_cleanup

        monkeypatch.setattr(data_cleanup, "AsyncSessionLocal", factory)
        async with factory() as db:
            db.add_all([
                PluginStatistics(plugin_id="stale", count=1, date=None),
                PluginStatistics(plugin_id="keep", count=100, date=None),
                SubscribeShare(share_title="旧分享", name="旧媒体", date="2000-01-01 00:00:00"),
                SubscribeShare(share_title="保留", name="新媒体", date="2099-01-01 00:00:00"),
                SubscribeStatistics(name="低频", count=1, year="2020"),
                SubscribeStatistics(name="保留", count=1000, year="2020"),
            ])
            await db.commit()
        csrf = await login(client)
        preview = (await client.get("/admin/api/maintenance/preview")).json()
        assert preview == {"plugins": 1, "shares": 1, "subscriptions": 1}
        rejected = await client.post("/admin/api/maintenance/cleanup", headers=csrf, json={"confirm": ""})
        assert rejected.status_code == 422
        result = (await client.post("/admin/api/maintenance/cleanup", headers=csrf, json={"confirm": "清理"})).json()
        assert result == {"status": "success", **preview}
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(PluginStatistics)) == 1
            assert await db.scalar(select(func.count()).select_from(SubscribeShare)) == 1
            assert await db.scalar(select(func.count()).select_from(SubscribeStatistics)) == 1
            audit = await db.scalar(select(AdminAudit).where(AdminAudit.action == "maintenance.cleanup"))
            assert audit.target == "success" and json.loads(audit.detail) == preview
        # 分布式锁冲突必须明确反馈，不能报告成成功执行。
        await redis.set(data_cleanup.data_cleanup_service._cleanup_lock_key(), "another-worker", ex=60)
        assert (await client.post("/admin/api/maintenance/cleanup", headers=csrf, json={"confirm": "清理"})).status_code == 409
    run_scenario(monkeypatch, scenario)


def test_media_filter_uses_canonical_sources_and_extensions(monkeypatch):
    """来源别名按宿主协议规范化，插件扩展来源也可从筛选选项查询。"""
    async def scenario(client, factory, redis):
        """验证来源筛选不会把 TMDB 别名误当成数据库中的原始标识。"""
        async with factory() as db:
            db.add_all([
                SubscribeShare(name="TMDB", share_title="TMDB 分享", media_source="themoviedb", media_id="1", type="电影"),
                SubscribeShare(name="扩展", share_title="扩展分享", media_source="my-source", media_id="2", type="音乐"),
            ])
            await db.commit()
        await login(client)
        result = (await client.get("/admin/api/data/subscribe-shares", params={"source": "tmdb"})).json()
        assert result["total"] == 1 and result["items"][0]["name"] == "TMDB"
        assert result["filters"]["sources"] == ["my-source", "themoviedb"]
        result = (await client.get("/admin/api/data/subscribe-shares", params={"source": "my-source", "media_type": "音乐"})).json()
        assert result["total"] == 1 and result["items"][0]["name"] == "扩展"
    run_scenario(monkeypatch, scenario)


def test_secure_cookie_and_expired_sessions(monkeypatch):
    """生产 Cookie 属性、固定过期时间和损坏会话均在真实认证边界验证。"""
    async def scenario(client, factory, redis):
        """HTTPS 登录后过期 Redis 会话，旧 Cookie 不得继续访问数据。"""
        monkeypatch.setattr(settings, "ADMIN_COOKIE_SECURE", True)
        monkeypatch.setattr(settings, "ADMIN_SESSION_HOURS", 2)
        client.base_url = "https://test"
        response = await client.post("/admin/api/auth/login", json={"username": "root", "password": PASSWORD})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/admin" in cookie and "SameSite=strict" in cookie
        token = client.cookies.get(COOKIE_NAME)
        key = session_key(token)
        assert 0 < await redis.ttl(key) <= 7200
        assert token not in key
        assert (await client.get("/admin/api/auth/me")).status_code == 200
        await redis.set(key, "[]", ex=60)
        assert (await client.get("/admin/api/auth/me")).status_code == 401
        await redis.delete(key)
        assert (await client.get("/admin/api/overview")).status_code == 401
    run_scenario(monkeypatch, scenario)
