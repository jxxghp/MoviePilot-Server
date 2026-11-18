"""
知识元数据、真实后台鉴权、不可变草稿与并发维护的隔离回归。
"""

import asyncio
import json

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_mock_engine, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.api import router
from app.admin.auth import hash_password
from app.core.config import settings
from app.db import redis as redis_module
from app.db.deps import get_db
from app.models import Base
from app.models.admin import AdminAudit, AdminUser
from app.models.knowledge import (
    KnowledgeDocument, KnowledgeRevision, KnowledgeSite, KnowledgeSiteName, KnowledgeSource,
)

PASSWORD = "synthetic-knowledge-password-123"
ROOT = "/admin/api/knowledge"


def run_scenario(monkeypatch, scenario, *, database_url="sqlite+aiosqlite:///:memory:", extra_router=None,
                 connect_args=None):
    """
    使用真实 ASGI、管理员会话和外键校验，不访问运行期 SQL 或 Redis。
    """
    async def run():
        """
        每个场景在独立事件循环内创建并关闭临时资源。
        """
        engine = create_async_engine(database_url, connect_args=connect_args or {})

        if engine.dialect.name == "sqlite":
            @event.listens_for(engine.sync_engine, "connect")
            def foreign_keys(connection, _record):
                """
                打开 SQLite 外键，避免本地测试掩盖 PostgreSQL 的父子写入问题。
                """
                connection.execute("PRAGMA foreign_keys=ON")

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "ADMIN_COOKIE_SECURE", False)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", False)
        encoded = await hash_password(PASSWORD)
        async with sessions() as db:
            for username, role in (("root", "super_admin"), ("editor", "admin")):
                db.add(AdminUser(username=username, display_name=username, password_hash=encoded, role=role,
                                 enabled=True, must_change_password=False, auth_version=1, created_at="2026-11-18"))
            await db.commit()

        async def isolated_db():
            """
            保留鉴权依赖，仅把数据库连接替换为测试会话。
            """
            async with sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        if extra_router is not None:
            app.include_router(extra_router)
        app.dependency_overrides[get_db] = isolated_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await scenario(client, sessions, redis)
        finally:
            await redis.aclose()
            await engine.dispose()
    asyncio.run(run())


async def login(client, username="root"):
    """
    通过真实登录取得 Cookie 与 CSRF，不用伪造授权依赖。
    """
    response = await client.post("/admin/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


async def collection(client, headers, *, slug="mp-guide", **overrides):
    """
    创建仅供当前场景使用的合成集合。
    """
    response = await client.post(ROOT + "/collections", headers=headers, json={
        "slug": slug, "name": "合成使用指南", "domain": "mp", "product": "moviepilot", **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


async def manual_source(client, headers, collection_id):
    """
    创建不会触发网络请求的手工来源。
    """
    response = await client.post(ROOT + "/sources", headers=headers, json={
        "collection_id": collection_id, "name": "合成手工来源", "connector": "manual",
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_current_details_dependency_flags_and_source_filters(monkeypatch):
    """编辑器按 ID 得到最新版本和身份锁定，筛选在 SQL 分页前生效。"""
    async def scenario(client, _sessions, _redis):
        """普通管理员可维护元数据，匿名、缺失记录和无效筛选仍受统一边界保护。"""
        assert (await client.get(ROOT + "/collections/" + "a" * 32)).status_code == 401
        headers = await login(client, "editor")
        parent = await collection(client, headers)
        detail = await client.get(ROOT + "/collections/" + parent["id"])
        assert detail.json()["version"] == 1 and detail.json()["has_sources"] is False
        registered = (await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成站点", "domain": "synthetic.example", "aliases": ["合成别名", "synthetic-alias"],
        })).json()
        origin = (await client.post(ROOT + "/sources", headers=headers, json={
            "name": "合成 HTTP", "collection_id": parent["id"], "site_id": registered["id"],
            "connector": "http", "url": "https://synthetic.example/rules",
        })).json()
        manual = await manual_source(client, headers, parent["id"])
        assert (await client.get(ROOT + "/collections/" + parent["id"])).json()["has_sources"] is True
        site = (await client.get(ROOT + "/sites/" + registered["id"])).json()
        assert site["has_sources"] is True and set(site["aliases"]) == {"合成别名", "synthetic-alias"}
        source = await client.get(ROOT + "/sources/" + origin["id"])
        assert source.headers["cache-control"] == "no-store"
        assert source.json()["has_documents"] is False and source.json()["has_credential"] is False
        filtered = (await client.get(ROOT + "/sources", params={"site_id": registered["id"], "size": 1})).json()
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == origin["id"]
        filtered = (await client.get(ROOT + "/sources", params={"connector": "manual", "size": 1})).json()
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == manual["id"]
        document = await client.post(ROOT + "/documents", headers=headers, json={
            "source_id": manual["id"], "external_id": "synthetic", "title": "合成知识", "content": "合成正文",
        })
        assert document.status_code == 201
        assert (await client.get(ROOT + "/sources/" + manual["id"])).json()["has_documents"] is True
        bad = await client.get(ROOT + "/sources", params={"connector": "do-not-echo-this-value"})
        assert bad.status_code == 422 and "do-not-echo-this-value" not in bad.text
        for kind in ("collections", "sites", "sources"):
            missing = await client.get(ROOT + "/" + kind + "/" + "f" * 32)
            assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
    run_scenario(monkeypatch, scenario)


def test_job_detail_is_current_and_excludes_lease_secrets(monkeypatch):
    """任务深链不依赖列表页，详情仍只包含公开给管理员的任务投影。"""
    async def scenario(client, _sessions, _redis):
        """手动排队、取消后按同一 ID 读取当前状态与版本。"""
        headers = await login(client, "editor")
        queued = (await client.post(ROOT + "/jobs/index", headers=headers, json={})).json()
        detail = await client.get(ROOT + "/jobs/" + queued["id"])
        assert detail.status_code == 200 and detail.json() == queued
        assert detail.headers["cache-control"] == "no-store"
        assert not {"lease_token", "lease_expires_at", "path", "payload"} & detail.json().keys()
        cancelled = await client.post(ROOT + "/jobs/" + queued["id"] + "/cancel", headers=headers,
                                      json={"expected_version": queued["version"]})
        current = (await client.get(ROOT + "/jobs/" + queued["id"])).json()
        assert cancelled.status_code == 200 and current["status"] == "cancelled" and current["version"] > queued["version"]
        assert (await client.get(ROOT + "/jobs/" + "a" * 32)).status_code == 404
    run_scenario(monkeypatch, scenario)


def test_admin_session_csrf_disabled_service_and_audit(monkeypatch):
    """
    在线服务关闭时可维护草稿，匿名、跨站和失效角色仍不能访问。
    """
    async def scenario(client, sessions, redis):
        """
        核对真实权限拒绝、有限列表、并发版本与审计不含正文。
        """
        assert (await client.get(ROOT + "/status")).status_code == 401
        assert (await client.post(ROOT + "/collections", json={})).status_code == 401
        headers = await login(client, "editor")
        assert (await client.get(ROOT + "/status")).json()["enabled"] is False
        assert (await client.post(ROOT + "/collections", json={})).status_code == 403
        item = await collection(client, headers)
        result = await client.get(ROOT + "/collections", params={"size": 1})
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json()["total"] == 1 and result.json()["items"][0]["id"] == item["id"]
        assert (await client.get(ROOT + "/collections", params={"size": 101})).status_code == 422
        update = {key: item[key] for key in ("slug", "name", "domain", "product", "description", "enabled")}
        response = await client.put(ROOT + "/collections/" + item["id"], headers=headers,
                                    json={**update, "name": "维护后的指南", "expected_version": item["version"]})
        assert response.status_code == 200 and response.json()["version"] == 2
        conflict = await client.put(ROOT + "/collections/" + item["id"], headers=headers,
                                    json={**update, "expected_version": 1})
        assert conflict.status_code == 409
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(AdminAudit).where(
                AdminAudit.action.like("knowledge.%"))) == 2
            actor = await db.scalar(select(AdminUser).where(AdminUser.username == "editor"))
            actor.enabled = False
            await db.commit()
        assert (await client.get(ROOT + "/sources")).status_code == 401
        assert await redis.keys("*admin:session:*")
    run_scenario(monkeypatch, scenario)


def test_site_alias_collision_rolls_back_whole_update(monkeypatch):
    """
    全半角和大小写统一，别名冲突不破坏原名称或递增版本。
    """
    async def scenario(client, sessions, _redis):
        """
        通过真实 SQL 外键和唯一约束验证创建与替换的原子性。
        """
        headers = await login(client)
        first = await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成甲站", "domain": "FIRST.example", "aliases": ["甲站", "ＡＵＤ", "aud"],
        })
        assert first.status_code == 201
        site = first.json()
        assert site["domain"] == "first.example" and site["aliases"] == ["甲站", "ＡＵＤ"]
        second = await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成乙站", "domain": "second.example", "aliases": ["乙站"],
        })
        assert second.status_code == 201
        target = second.json()
        duplicate = await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成丙站", "domain": "third.example", "aliases": [" aUd "],
        })
        assert duplicate.status_code == 409
        collision = await client.put(ROOT + "/sites/" + target["id"], headers=headers, json={
            "name": "已修改的乙站", "domain": target["domain"], "aliases": ["甲站"], "expected_version": 1,
        })
        assert collision.status_code == 409
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeSite)) == 2
            row = await db.get(KnowledgeSite, target["id"])
            assert row.name == "合成乙站" and row.version == 1
            alias = await db.get(KnowledgeSiteName, "乙站")
            assert alias.site_id == target["id"]
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("url", [
    "https://user:do-not-echo-this-password@source.example/rules",
    "https://source.example/rules?passkey=do-not-echo-this-password",
    "http://source.example/rules", "https://127.0.0.1/rules",
    "https://metadata.internal/rules", "https://source.example:8443/rules",
])
def test_source_rejects_credential_or_private_urls_without_echo(monkeypatch, url):
    """
    无效来源和误填凭据不落库，也不通过验证详情或审计回显。
    """
    async def scenario(client, sessions, _redis):
        """
        422 不包含输入秘密，知识服务没有为保存配置访问外部网络。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        response = await client.post(ROOT + "/sources", headers=headers, json={
            "collection_id": parent["id"], "name": "合成网页来源", "connector": "http", "url": url,
        })
        assert response.status_code == 422
        assert "do-not-echo-this-password" not in response.text
        extra = await client.post(ROOT + "/sources", headers=headers, json={
            "collection_id": parent["id"], "name": "误填字段", "connector": "manual",
            "cookie": "do-not-echo-this-password",
        })
        assert extra.status_code == 422 and "do-not-echo-this-password" not in extra.text
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeSource)) == 0
            details = (await db.scalars(select(AdminAudit.detail))).all()
            assert "do-not-echo-this-password" not in json.dumps(details)
    run_scenario(monkeypatch, scenario)


def test_source_ownership_validation_and_bounded_paging(monkeypatch):
    """
    来源必须指向现存集合和站点，不能用普通表单改变既有文档归属。
    """
    async def scenario(client, sessions, _redis):
        """
        核对未知父记录、连接器参数和有文档后的身份保护。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        absent = await client.post(ROOT + "/sources", headers=headers, json={
            "collection_id": "f" * 32, "name": "不存在的集合", "connector": "manual",
        })
        assert absent.status_code == 404
        for changes in ({"connector": "github", "repository": "owner/repo"},
                        {"connector": "manual", "refresh_minutes": 10},
                        {"connector": "http", "url": "https://source.example/", "paths": ["../secret"]},
                        {"connector": "http", "url": "https://source.example/", "paths": ["//elsewhere.example/"]},
                        {"connector": "http", "url": "https://source.example/", "paths": ["rules?token=secret"]}):
            response = await client.post(ROOT + "/sources", headers=headers, json={
                "collection_id": parent["id"], "name": "参数检查", **changes,
            })
            assert response.status_code == 422, response.text
        source = await manual_source(client, headers, parent["id"])
        created = await client.post(ROOT + "/documents", headers=headers, json={
            "source_id": source["id"], "external_id": "guide", "title": "合成规则", "content": "仅用于测试",
        })
        assert created.status_code == 201
        other = await collection(client, headers, slug="other-guide")
        fields = {key: source[key] for key in ("collection_id", "site_id", "name", "connector", "url",
                                                "repository", "branch", "paths", "selector", "refresh_minutes", "enabled")}
        moved = await client.put(ROOT + "/sources/" + source["id"], headers=headers, json={
            **fields, "collection_id": other["id"], "expected_version": 1,
        })
        assert moved.status_code == 409
        async with sessions() as db:
            assert (await db.get(KnowledgeSource, source["id"])).collection_id == parent["id"]
        assert (await client.get(ROOT + "/sources", params={"collection_id": other["id"]})).json()["total"] == 0
    run_scenario(monkeypatch, scenario)


def test_drafts_append_immutable_history_and_preserve_unknown_provenance(monkeypatch):
    """
    修改追加新草稿，列表和审计不带正文，未知时间不被录入时间替代。
    """
    async def scenario(client, sessions, _redis):
        """
        核对重复身份、版本冲突、历史回读和没有自动发布。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        source = await manual_source(client, headers, parent["id"])
        body = {"source_id": source["id"], "external_id": "synthetic-guide", "title": "合成指南",
                "content": "\n    只属于当前测试的旧知识正文\n\n", "source_url": "https://docs.example/guide"}
        response = await client.post(ROOT + "/documents", headers=headers, json=body)
        assert response.status_code == 201, response.text
        document = response.json()
        assert document["published_revision"] is None and document["revision"]["state"] == "draft"
        assert document["revision"]["fetched_at"] is None and document["revision"]["version_scope"] == "unknown"
        assert document["revision"]["product"] == "moviepilot"
        assert (await client.post(ROOT + "/documents", headers=headers, json=body)).status_code == 409
        revision = {"title": "修订后的合成指南", "content": "只属于当前测试的新知识正文", "expected_version": 1,
                    "version_scope": "range", "min_version": "v3.2", "max_version": "3.2.9"}
        response = await client.post(ROOT + "/documents/" + document["id"] + "/revisions", headers=headers, json=revision)
        assert response.status_code == 201, response.text
        changed = response.json()
        assert changed["version"] == 2 and changed["head_revision"] == 2 and changed["published_revision"] is None
        assert changed["revision"]["min_version"] == "3.2.0"
        assert (await client.post(ROOT + "/documents/" + document["id"] + "/revisions",
                                  headers=headers, json=revision)).status_code == 409
        old = (await client.get(ROOT + "/documents/" + document["id"], params={"revision": 1})).json()
        assert old["revision"]["content"] == body["content"] and old["revision"]["number"] == 1
        current = (await client.get(ROOT + "/documents/" + document["id"])).json()
        assert current["revision"]["content"] == revision["content"]
        listed = (await client.get(ROOT + "/documents", params={"q": "修订", "source_id": source["id"]})).json()
        assert listed["total"] == 1 and "content" not in listed["items"][0]["revision"]
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
            assert await db.scalar(select(func.count()).select_from(KnowledgeRevision)) == 2
            audit = (await db.scalars(select(AdminAudit.detail))).all()
            assert body["content"] not in json.dumps(audit) and revision["content"] not in json.dumps(audit)
        assert (await client.get("/knowledge/v1/search")).status_code == 404
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("overrides", [
    {"version_scope": "range"},
    {"version_scope": "all", "min_version": "3.0.0"},
    {"version_scope": "range", "min_version": "3.3.0", "max_version": "3.2.0"},
    {"fetched_at": "2026-11-18T12:00:00"},
    {"product": "moviepilot-neo"},
    {"state": "published"},
    {"content": " \n\t"},
])
def test_invalid_revision_metadata_cannot_create_documents(monkeypatch, overrides):
    """
    不接受错误适用性、无时区时间、跨产品正文或客户端伪造发布状态。
    """
    async def scenario(client, sessions, _redis):
        """
        验证失败后不产生空文档或审计成功记录。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        source = await manual_source(client, headers, parent["id"])
        response = await client.post(ROOT + "/documents", headers=headers, json={
            "source_id": source["id"], "external_id": "guide", "title": "合成指南", "content": "合成正文", **overrides,
        })
        assert response.status_code == 422, response.text
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
            assert await db.scalar(select(func.count()).select_from(KnowledgeRevision)) == 0
    run_scenario(monkeypatch, scenario)


def test_concurrent_revision_writes_have_one_winner(monkeypatch, tmp_path):
    """
    两个真实连接同时修改同一版本，只追加一份草稿和一条成功审计。
    """
    async def scenario(client, sessions, _redis):
        """
        文件 SQLite 允许独立连接竞争，避免单连接内存库掩盖并发覆盖。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        source = await manual_source(client, headers, parent["id"])
        created = await client.post(ROOT + "/documents", headers=headers, json={
            "source_id": source["id"], "external_id": "race", "title": "并发草稿", "content": "原始正文",
        })
        document = created.json()
        path = ROOT + "/documents/" + document["id"] + "/revisions"
        results = await asyncio.gather(*(
            client.post(path, headers=headers, json={"title": title, "content": title, "expected_version": 1})
            for title in ("维护者甲", "维护者乙")
        ))
        assert sorted(result.status_code for result in results) == [201, 409]
        async with sessions() as db:
            stored = await db.get(KnowledgeDocument, document["id"])
            assert stored.version == 2 and stored.head_revision == 2
            assert await db.scalar(select(func.count()).select_from(KnowledgeRevision)) == 2
            assert await db.scalar(select(func.count()).select_from(AdminAudit).where(
                AdminAudit.action == "knowledge.document.revise")) == 1
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'knowledge-race.db'}")


def test_knowledge_tables_compile_with_postgresql():
    """
    用 PostgreSQL 方言检查新增表与约束，不冒充真实 PostgreSQL 集成测试。
    """
    statements = []

    def capture(statement, *args, **kwargs):
        """
        记录真实方言编译结果，不创建网络连接或生产数据库。
        """
        statements.append(str(statement.compile(dialect=engine.dialect)))

    engine = create_mock_engine("postgresql://", capture)
    expected = {"KNOWLEDGE_COLLECTION", "KNOWLEDGE_SITE", "KNOWLEDGE_SITE_NAME",
                "KNOWLEDGE_SOURCE", "KNOWLEDGE_DOCUMENT", "KNOWLEDGE_REVISION"}
    tables = [table for table in Base.metadata.sorted_tables if table.name in expected]
    Base.metadata.create_all(engine, tables=tables, checkfirst=False)
    ddl = "\n".join(statements)
    assert {table.name for table in tables} == expected and 'CREATE TABLE "KNOWLEDGE_REVISION"' in ddl
    assert "uq_knowledge_document_identity" in ddl and "FOREIGN KEY" in ddl


def test_supported_source_forms_roundtrip_without_network(monkeypatch):
    """
    合法网页、GitHub 和站点来源可维护，保存与回读不会现场联网。
    """
    async def forbidden_network(*args, **kwargs):
        """
        任意真实 HTTP 出站都使本场景失败，ASGI 测试请求不受影响。
        """
        raise AssertionError("保存知识来源不能联网")

    from httpx import AsyncHTTPTransport
    monkeypatch.setattr(AsyncHTTPTransport, "handle_async_request", forbidden_network)

    async def scenario(client, _sessions, _redis):
        """
        校验非敏感配置往返以及普通版本更新。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        site = (await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成测试站", "domain": "tracker.example", "aliases": ["测试站"],
        })).json()
        for form in (
            {"connector": "github", "repository": "test-owner/knowledge-docs", "branch": "v3",
             "paths": ["README.md", "docs/usage.md"]},
            {"connector": "http", "url": "https://docs.example/guide", "selector": "main"},
            {"connector": "nexusphp", "url": "https://tracker.example/", "site_id": site["id"],
             "paths": ["rules.php", "forums.php?action=viewtopic&topicid=12"]},
        ):
            response = await client.post(ROOT + "/sources", headers=headers, json={
                "collection_id": parent["id"], "name": "合成来源", **form,
            })
            assert response.status_code == 201, response.text
            stored = response.json()
            assert stored["enabled"] is False and stored["version"] == 1
            body = {key: stored[key] for key in ("collection_id", "site_id", "name", "connector", "url",
                                                 "repository", "branch", "paths", "selector", "refresh_minutes", "enabled")}
            changed = await client.put(ROOT + "/sources/" + stored["id"], headers=headers,
                                       json={**body, "name": "改名后的来源", "expected_version": 1})
            assert changed.status_code == 200 and changed.json()["version"] == 2
            assert changed.json()["paths"] == form.get("paths", [])
        listed = (await client.get(ROOT + "/sources", params={"collection_id": parent["id"]})).json()
        assert listed["total"] == 3 and len(listed["items"]) == 3
    run_scenario(monkeypatch, scenario)
