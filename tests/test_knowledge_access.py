"""
独立知识凭据的真实会话管理、集合授权与逐请求认证回归，全部使用合成数据。
"""

import asyncio
import hashlib
import json
import re

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import create_mock_engine, func, select

from app.core.config import settings
from app.models import Base
from app.models.admin import AdminAudit, AdminUser
from app.models.knowledge import KnowledgeClient, KnowledgeGrant
from app.services.knowledge.access import KnowledgePrincipal, knowledge_principal
from test_knowledge_catalog import ROOT, collection, login, run_scenario as catalog_scenario

probe = APIRouter(prefix="/knowledge-access-test")
CLIENTS = ROOT + "/clients"


@probe.get("")
async def identity(principal: KnowledgePrincipal = Depends(knowledge_principal)):
    """
    仅挂载到本文件临时 ASGI 应用的认证探针，产品不注册此路由。
    """
    return {"client_id": principal.client_id, "version": principal.version,
            "collections": list(principal.collection_ids)}


@probe.get("/{collection_id}")
async def collection_access(collection_id: str, principal: KnowledgePrincipal = Depends(knowledge_principal)):
    """
    模拟按实际文档集合执行二次权限检查，不依赖客户端自报范围。
    """
    principal.require_collection(collection_id)
    return {"allowed": True}


def run_scenario(monkeypatch, scenario, **kwargs):
    """
    复用真实后台 Cookie、CSRF、SQL 和 Redis 的隔离环境，只增加测试探针。
    """
    return catalog_scenario(monkeypatch, scenario, extra_router=probe, **kwargs)


def bearer(token: str) -> dict:
    """
    按最终检索协议发送独立 Bearer 凭据。
    """
    return {"Authorization": "Bearer " + token}


async def issue(client, headers, collection_ids=(), **overrides):
    """
    通过超级管理员真实维护接口签发测试客户端。
    """
    response = await client.post(CLIENTS, headers=headers, json={
        "name": "合成测试实例", "collection_ids": list(collection_ids), **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


def update_payload(item: dict, **overrides) -> dict:
    """
    从管理投影构造完整更新表单，不把只读字段或首显令牌带回服务端。
    """
    return {**{key: item[key] for key in ("name", "enabled", "expires_at", "collection_ids")},
            "expected_version": item["version"], **overrides}


def test_management_requires_super_admin_and_csrf(monkeypatch):
    """
    普通管理员、匿名、自报身份与缺失 CSRF 均不能签发或变更集合授权。
    """
    async def scenario(client, sessions, _redis):
        """
        覆盖每条管理路由，并验证账号降权对已有 Cookie 立即生效。
        """
        assert (await client.get(CLIENTS)).status_code == 401
        assert (await client.post(CLIENTS, json={"name": "匿名"})).status_code == 401
        headers = await login(client, "editor")
        for method, path, payload in (
            ("GET", CLIENTS, None), ("GET", CLIENTS + "/unknown", None),
            ("POST", CLIENTS, {"name": "越权"}),
            ("PUT", CLIENTS + "/unknown", {"name": "越权", "expected_version": 1}),
            ("POST", CLIENTS + "/unknown/rotate", {"expected_version": 1}),
        ):
            result = await client.request(method, path, headers=headers, json=payload)
            assert result.status_code == 403, result.text
        headers = await login(client)
        parent = await collection(client, headers)
        assert (await client.post(CLIENTS, json={"name": "无 CSRF"})).status_code == 403
        issued = await issue(client, headers, [parent["id"]])
        path = CLIENTS + "/" + issued["id"]
        assert (await client.put(path, json=update_payload(issued))).status_code == 403
        assert (await client.post(path + "/rotate", json={"expected_version": 1})).status_code == 403
        async with sessions() as db:
            root = await db.scalar(select(AdminUser).where(AdminUser.username == "root"))
            root.role = "admin"
            await db.commit()
        assert (await client.get(path)).status_code == 403
        assert (await client.post(path + "/rotate", headers=headers, json={"expected_version": 1})).status_code == 403
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeClient)) == 1
    run_scenario(monkeypatch, scenario)


def test_issue_once_digest_only_and_safe_metadata(monkeypatch):
    """
    新令牌只出现在签发响应；回读、更新、列表、摘要和审计均无明文。
    """
    async def scenario(client, sessions, _redis):
        """
        在服务关闭时预先维护，开启后用真正 HTTP Bearer 认证测试探针。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        issued_response = await client.post(CLIENTS, headers=headers, json={
            "name": "书房 MP", "collection_ids": [parent["id"]], "expires_at": "2099-01-01T08:00:00+08:00",
        })
        assert issued_response.status_code == 201
        assert issued_response.headers["cache-control"] == "no-store"
        issued = issued_response.json()
        token = issued["token"]
        assert re.fullmatch(r"mpk_[A-Za-z0-9_-]{43}", token)
        assert issued["expires_at"] == "2099-01-01T00:00:00Z"
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        path = CLIENTS + "/" + issued["id"]
        for response in (await client.get(path), await client.get(CLIENTS, params={"q": "书房"})):
            assert response.status_code == 200
            assert token not in response.text and fingerprint not in response.text
            assert "token" not in response.text and response.headers["cache-control"] == "no-store"
        result = await client.put(path, headers=headers, json=update_payload(issued, name="书房 MP 二"))
        assert result.status_code == 200 and result.json()["version"] == 2
        assert token not in result.text and "token" not in result.text
        async with sessions() as db:
            stored = await db.get(KnowledgeClient, issued["id"])
            assert stored.token_hash == fingerprint and token != stored.token_hash
            assert await db.scalar(select(func.count()).select_from(KnowledgeGrant)) == 1
            audits = (await db.scalars(select(AdminAudit).where(AdminAudit.action.like("knowledge.client.%")))).all()
            serialized = json.dumps([{"actor": row.actor, "detail": row.detail, "target": row.target} for row in audits])
            assert token not in serialized and fingerprint not in serialized and len(audits) == 2
        unavailable = await client.get("/knowledge-access-test", headers=bearer(token))
        assert unavailable.status_code == 503
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        allowed = await client.get("/knowledge-access-test", headers={
            **bearer(token), "X-MoviePilot-UID": "claiming-another-user", "X-GitHub-User": "claiming-root",
        })
        assert allowed.json() == {"client_id": issued["id"], "version": 2, "collections": [parent["id"]]}
        assert token not in allowed.text and fingerprint not in allowed.text
        assert (await client.get("/knowledge-access-test/" + parent["id"], headers=bearer(token))).status_code == 200
        assert (await client.get("/knowledge-access-test/" + "f" * 32, headers=bearer(token))).status_code == 403
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("authorization", [
    None, "Basic mpk_" + "A" * 43, "Bearer mpa_" + "A" * 43,
    "Bearer mpk_short", "Bearer mpk_" + "A" * 43, "Bearer mpk_" + "A" * 44,
])
def test_rejects_other_credentials_and_self_claims(monkeypatch, authorization):
    """
    后台 Cookie、推送令牌、伪造令牌和自报用户名都不形成知识读取身份。
    """
    async def scenario(client, _sessions, _redis):
        """
        即使已经登录超级管理员，知识读取仍要求独立的有效 Bearer。
        """
        await login(client)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        headers = {"X-MoviePilot-UID": "root", "X-GitHub-User": "admin"}
        if authorization is not None:
            headers["Authorization"] = authorization
        response = await client.get("/knowledge-access-test", headers=headers)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
        assert authorization is None or authorization not in response.text
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("overrides", [
    {"expires_at": "2099-01-01T12:00:00"}, {"collection_ids": ["f" * 32]},
    {"collection_ids": ["bad-id"]}, {"collection_ids": ["f" * 32] * 2},
    {"collection_ids": ["f" * 32] * 101}, {"token": "synthetic-secret-do-not-echo"},
    {"github_user": "root"}, {"role": "super_admin"}, {"enabled": True, "name": " "},
])
def test_invalid_client_input_never_issues_or_echoes(monkeypatch, overrides):
    """
    无效范围、无时区时限和非白名单身份字段在落库前被拒绝。
    """
    async def scenario(client, sessions, _redis):
        """
        请求校验错误不回显误填的秘密，且没有半个客户端或授权记录。
        """
        headers = await login(client)
        result = await client.post(CLIENTS, headers=headers, json={"name": "合成实例", **overrides})
        assert result.status_code == 422 and "synthetic-secret-do-not-echo" not in result.text
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeClient)) == 0
            assert await db.scalar(select(func.count()).select_from(KnowledgeGrant)) == 0
    run_scenario(monkeypatch, scenario)


def test_collection_grants_are_explicit_fresh_and_not_wildcards(monkeypatch):
    """
    空范围不授予全部集合，停用集合实时剔除且不能向新客户端再授权。
    """
    async def scenario(client, _sessions, _redis):
        """
        保留已有停用授权便于维护，但无法通过保留记录继续读取。
        """
        headers = await login(client)
        first = await collection(client, headers)
        second = await collection(client, headers, slug="pt-rules", domain="pt", product="")
        issued = await issue(client, headers, [first["id"], second["id"]])
        empty = await issue(client, headers)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        assert (await client.get("/knowledge-access-test", headers=bearer(empty["token"]))).status_code == 403
        disabled = await client.put(ROOT + "/collections/" + first["id"], headers=headers, json={
            **{key: first[key] for key in ("slug", "name", "description", "domain", "product")},
            "enabled": False, "expected_version": first["version"],
        })
        assert disabled.status_code == 200
        allowed = await client.get("/knowledge-access-test", headers=bearer(issued["token"]))
        assert allowed.json()["collections"] == [second["id"]]
        assert (await client.get("/knowledge-access-test/" + first["id"],
                                 headers=bearer(issued["token"]))).status_code == 403
        denied = await client.post(CLIENTS, headers=headers, json={"name": "无权新增", "collection_ids": [first["id"]]})
        assert denied.status_code == 422
        path = CLIENTS + "/" + issued["id"]
        retained = await client.put(path, headers=headers, json=update_payload(issued, name="保留已停用集合记录"))
        assert retained.status_code == 200 and sorted(retained.json()["collection_ids"]) == sorted(issued["collection_ids"])
        removed = await client.put(path, headers=headers, json=update_payload(retained.json(), collection_ids=[]))
        assert removed.status_code == 200
        assert (await client.get("/knowledge-access-test", headers=bearer(issued["token"]))).status_code == 403
        # 已从范围移除的停用集合不再是可保留授权。
        denied = await client.put(path, headers=headers, json=update_payload(removed.json(), collection_ids=[first["id"]]))
        assert denied.status_code == 422
        assert (await client.get(path)).json()["version"] == 3
    run_scenario(monkeypatch, scenario)


def test_expiry_disabling_and_rotation_affect_following_requests(monkeypatch):
    """
    时限、停用和轮换都检查当前数据库状态，旧表单不能复活或覆盖新令牌。
    """
    async def scenario(client, sessions, _redis):
        """
        轮换响应丢失后不能找回明文；刷新后可明确再次轮换并使用新版本。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        issued = await issue(client, headers, [parent["id"]], expires_at="2020-01-01T00:00:00Z")
        path = CLIENTS + "/" + issued["id"]
        token = issued["token"]
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        assert (await client.get("/knowledge-access-test", headers=bearer(token))).status_code == 401
        refreshed = await client.put(path, headers=headers, json=update_payload(issued, expires_at=None))
        assert refreshed.status_code == 200
        assert (await client.get("/knowledge-access-test", headers=bearer(token))).status_code == 200
        disabled = await client.put(path, headers=headers, json=update_payload(refreshed.json(), enabled=False))
        assert disabled.status_code == 200
        assert (await client.get("/knowledge-access-test", headers=bearer(token))).status_code == 401
        restored = await client.put(path, headers=headers, json=update_payload(disabled.json(), enabled=True))
        assert restored.status_code == 200
        rotated = await client.post(path + "/rotate", headers=headers, json={"expected_version": 4})
        assert rotated.status_code == 200 and rotated.json()["version"] == 5
        assert rotated.headers["cache-control"] == "no-store" and rotated.json()["token"] != token
        assert (await client.get("/knowledge-access-test", headers=bearer(token))).status_code == 401
        assert (await client.get("/knowledge-access-test", headers=bearer(rotated.json()["token"]))).status_code == 200
        for result in (
            await client.post(path + "/rotate", headers=headers, json={"expected_version": 4}),
            await client.put(path, headers=headers, json=update_payload(restored.json(), collection_ids=[])),
        ):
            assert result.status_code == 409
            assert result.headers["cache-control"] == "no-store"
            assert token not in result.text and rotated.json()["token"] not in result.text
        current = (await client.get(path)).json()
        assert "token" not in current and current["version"] == 5
        again = await client.post(path + "/rotate", headers=headers, json={"expected_version": current["version"]})
        assert again.status_code == 200 and again.json()["version"] == 6
        assert (await client.get("/knowledge-access-test", headers=bearer(rotated.json()["token"]))).status_code == 401
        assert (await client.get("/knowledge-access-test", headers=bearer(again.json()["token"]))).status_code == 200
        client.cookies.clear()
        assert (await client.get(path, headers=bearer(again.json()["token"]))).status_code == 401
        async with sessions() as db:
            stored = await db.get(KnowledgeClient, issued["id"])
            assert stored.token_hash == hashlib.sha256(again.json()["token"].encode()).hexdigest()
            assert await db.scalar(select(func.count()).select_from(AdminAudit).where(
                AdminAudit.action == "knowledge.client.rotate")) == 2
    run_scenario(monkeypatch, scenario)


def test_concurrent_rotation_has_one_winner_and_no_lost_grants(monkeypatch, tmp_path):
    """
    两个独立数据库连接轮换同一版本，只签发一个有效新令牌和一条审计。
    """
    async def scenario(client, sessions, _redis):
        """
        文件库模拟多 worker 写入；失败响应不泄露已生成但未提交的令牌。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        issued = await issue(client, headers, [parent["id"]])
        path = CLIENTS + "/" + issued["id"] + "/rotate"
        results = await asyncio.gather(*(
            client.post(path, headers=headers, json={"expected_version": 1}) for _ in range(2)
        ))
        assert sorted(result.status_code for result in results) == [200, 409]
        winner = next(result.json() for result in results if result.status_code == 200)
        loser = next(result for result in results if result.status_code == 409)
        assert "token" not in loser.text and winner["version"] == 2
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        assert (await client.get("/knowledge-access-test", headers=bearer(issued["token"]))).status_code == 401
        assert (await client.get("/knowledge-access-test", headers=bearer(winner["token"]))).status_code == 200
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeGrant)) == 1
            assert await db.scalar(select(func.count()).select_from(AdminAudit).where(
                AdminAudit.action == "knowledge.client.rotate")) == 1
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'knowledge-access-race.db'}")


def test_concurrent_grant_edit_and_rotation_share_one_version(monkeypatch, tmp_path):
    """
    授权编辑与令牌轮换竞争同一并发版本，失败编辑不会半途删除原授权。
    """
    async def scenario(client, sessions, _redis):
        """
        按数据库实际赢家核对令牌、范围和审计，防止状态与权限分别覆盖。
        """
        headers = await login(client)
        first = await collection(client, headers)
        second = await collection(client, headers, slug="other-guide")
        issued = await issue(client, headers, [first["id"]])
        path = CLIENTS + "/" + issued["id"]
        edited, rotated = await asyncio.gather(
            client.put(path, headers=headers, json=update_payload(issued, collection_ids=[second["id"]])),
            client.post(path + "/rotate", headers=headers, json={"expected_version": 1}),
        )
        assert sorted([edited.status_code, rotated.status_code]) == [200, 409]
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        if edited.status_code == 200:
            token, expected_collection = issued["token"], second["id"]
            assert "token" not in edited.text
        else:
            token, expected_collection = rotated.json()["token"], first["id"]
            assert (await client.get("/knowledge-access-test", headers=bearer(issued["token"]))).status_code == 401
        allowed = await client.get("/knowledge-access-test", headers=bearer(token))
        assert allowed.json() == {"client_id": issued["id"], "version": 2, "collections": [expected_collection]}
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeGrant)) == 1
            assert await db.scalar(select(func.count()).select_from(AdminAudit).where(
                AdminAudit.action.in_(["knowledge.client.update", "knowledge.client.rotate"]))) == 1
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'knowledge-grant-race.db'}")


def test_access_tables_compile_with_postgresql():
    """
    PostgreSQL 方言验证客户端摘要唯一性及复合授权主键，不冒充数据库集成测试。
    """
    statements = []

    def capture(statement, *args, **kwargs):
        """
        收集方言编译的真实 DDL，测试不连接任何外部数据库。
        """
        statements.append(str(statement.compile(dialect=engine.dialect)))

    engine = create_mock_engine("postgresql://", capture)
    tables = [Base.metadata.tables[name] for name in ("KNOWLEDGE_CLIENT", "KNOWLEDGE_GRANT")]
    Base.metadata.create_all(engine, tables=tables, checkfirst=False)
    ddl = "\n".join(statements)
    assert "UNIQUE (token_hash)" in ddl and "PRIMARY KEY (client_id, collection_id)" in ddl
    assert 'REFERENCES "KNOWLEDGE_COLLECTION" (id)' in ddl and 'REFERENCES "KNOWLEDGE_CLIENT" (id)' in ddl
