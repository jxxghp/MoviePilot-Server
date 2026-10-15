"""用户权限与全局功能开关接口中社区勋章字段的测试。"""

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.user import router as user_router
from app.core.config import settings


def _get(path: str, params: dict | None = None) -> dict:
    """挂载用户路由并请求指定接口。"""

    async def run() -> dict:
        app = FastAPI()
        app.include_router(user_router, prefix="/user")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(path, params=params or {})
        assert response.status_code == 200
        return response.json()

    return asyncio.run(run())


def _preview(monkeypatch, preview_users: str, admins: str, github_user: str | None) -> dict:
    """按给定预览名单与管理员配置请求权限接口。"""
    monkeypatch.setattr(settings, "COMMUNITY_MEDALS_PREVIEW_USERS", preview_users)
    monkeypatch.setattr(settings, "MP_ADMIN_USERS", admins)
    params = {"github_user": github_user} if github_user is not None else None
    return _get("/user/permissions", params)


def test_community_medals_defaults():
    """默认全局开关关闭、预览名单为空。"""
    fields = type(settings).model_fields
    assert fields["COMMUNITY_MEDALS_ENABLED"].default is False
    assert fields["COMMUNITY_MEDALS_PREVIEW_USERS"].default == ""


def test_features_reflect_global_switch(monkeypatch):
    """全局功能接口只返回全局开关，忽略任何用户参数。"""
    monkeypatch.setattr(settings, "COMMUNITY_MEDALS_ENABLED", False)
    assert _get("/user/features") == {"community_medals": False}
    monkeypatch.setattr(settings, "COMMUNITY_MEDALS_ENABLED", True)
    assert _get("/user/features", {"github_user": "alice"}) == {"community_medals": True}


def test_permissions_no_longer_carry_global_switch(monkeypatch):
    """权限接口不再返回全局开关字段，预览与管理员身份解耦。"""
    monkeypatch.setattr(settings, "COMMUNITY_MEDALS_ENABLED", True)
    data = _preview(monkeypatch, "", "alice", "alice")
    assert "community_medals" not in data
    assert data["is_admin"] is True
    assert data["community_medals_preview"] is False


def test_empty_preview_list_means_nobody(monkeypatch):
    """预览名单为空时任何用户（含管理员与匿名）都不可预览。"""
    assert _preview(monkeypatch, "", "alice", "alice")["community_medals_preview"] is False
    assert _preview(monkeypatch, "", "", "bob")["community_medals_preview"] is False
    assert _preview(monkeypatch, "", "", None)["community_medals_preview"] is False


def test_listed_user_previews_case_insensitively(monkeypatch):
    """名单内用户可预览，比较不区分大小写并忽略空白；名单外用户与匿名不可预览。"""
    assert _preview(monkeypatch, "Alice, bob", "", " ALICE ")["community_medals_preview"] is True
    assert _preview(monkeypatch, "Alice, bob", "", "Bob")["community_medals_preview"] is True
    assert _preview(monkeypatch, "Alice, bob", "", "carol")["community_medals_preview"] is False
    assert _preview(monkeypatch, "Alice, bob", "", None)["community_medals_preview"] is False
    assert _preview(monkeypatch, "Alice, bob", "", "")["community_medals_preview"] is False
