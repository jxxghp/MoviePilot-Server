"""
合成来源凭据的实际后台维护、认证加密、部署轮换与并发版本保护。
"""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import settings
from app.models.admin import AdminAudit
from app.models.knowledge import KnowledgeCredential, KnowledgeSource
from app.services.knowledge.credentials import catalog, cipher
from app.services.knowledge.network import policy
from test_knowledge_catalog import ROOT, collection, login, run_scenario

SECRET = "session=synthetic-source-cookie-value"
FIELDS = ("collection_id", "site_id", "name", "connector", "url", "repository",
          "branch", "paths", "selector", "refresh_minutes", "enabled")


def keys(monkeypatch, tmp_path, *, active="first", names=("first",), mode=0o600):
    """写入外置临时目录的固定测试密钥，绝不接触真实部署密钥。"""
    location = tmp_path / "synthetic-keyring.json"
    location.write_text(json.dumps({"active": active, "keys": {
        name: base64.urlsafe_b64encode(bytes([number + 1]) * 32).decode() for number, name in enumerate(names)
    }}))
    location.chmod(mode)
    monkeypatch.setattr(settings, "KNOWLEDGE_KEY_FILE", str(location))
    return location


async def source(client, headers, *, connector="http"):
    """建立不出站的公开合成来源，GitHub 分支也只维护定位。"""
    parent = await collection(client, headers)
    payload = {"collection_id": parent["id"], "name": "合成认证来源", "connector": connector}
    if connector == "github":
        payload.update(repository="example/repository", paths=["README.md"])
    elif connector == "http":
        payload.update(url="https://docs.example/rules", paths=["faq"], selector="main")
    result = await client.post(ROOT + "/sources", headers=headers, json=payload)
    assert result.status_code == 201, result.text
    return result.json()


def action(item, **overrides):
    """使用状态回读中的两个版本，不能以零或旧表单跳过并发检查。"""
    return {"expected_source_version": item["source_version"], "expected_credential_version": item["version"], **overrides}


async def replace(client, headers, origin, *, secret=SECRET, kind="cookie", credential_version=0):
    """明确提交认证值到受保护的替换接口，而不是普通来源 JSON。"""
    return await client.put(ROOT + "/sources/" + origin["id"] + "/credential", headers=headers, json={
        "expected_source_version": origin["version"], "expected_credential_version": credential_version,
        "kind": kind, "secret": secret,
    })


def test_roles_csrf_ciphertext_and_metadata_never_return_secret(monkeypatch, tmp_path):
    """只有超级管理员能维护秘密，Cookie / CSRF / 密钥和源绑定都真实生效。"""
    async def scenario(client, sessions, _redis):
        """验证无部署密钥不写入，状态回读和审计均无明文、nonce 或密文。"""
        headers = await login(client)
        origin = await source(client, headers)
        path = ROOT + "/sources/" + origin["id"] + "/credential"
        monkeypatch.setattr(settings, "KNOWLEDGE_KEY_FILE", "")
        missing = await replace(client, headers, origin)
        assert missing.status_code == 503 and SECRET not in missing.text
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeCredential)) == 0
        assert (await client.put(path, json={"kind": "cookie", "secret": SECRET,
                                           "expected_source_version": 1, "expected_credential_version": 0})).status_code == 403
        editor = await login(client, "editor")
        assert (await replace(client, editor, origin)).status_code == 403
        for suffix in ("clear", "rewrap"):
            assert (await client.post(path + "/" + suffix, headers=editor, json={
                "expected_source_version": 1, "expected_credential_version": 0,
            })).status_code == 403
        keys(monkeypatch, tmp_path)
        headers = await login(client)
        saved = await replace(client, headers, origin)
        assert saved.status_code == 200, saved.text
        item = saved.json()
        assert item == {"configured": True, "state": "configured", "source_version": 2,
                        "version": 1, "kind": "cookie", "host": "docs.example"}
        for response in (saved, await client.get(path)):
            assert response.headers["cache-control"] == "no-store"
            assert all(value not in response.text for value in (SECRET, "ciphertext", "nonce", "key_id"))
        async with sessions() as db:
            row = await db.scalar(select(KnowledgeCredential))
            assert row.ciphertext != SECRET and SECRET not in row.ciphertext
            assert cipher.decrypt(row) == SECRET
            material = await catalog.material(db, await db.get(KnowledgeSource, origin["id"]))
            assert material.value.get_secret_value() == SECRET and SECRET not in repr(material)
            audits = (await db.scalars(select(AdminAudit).where(AdminAudit.action.like("knowledge.credential.%")))).all()
            assert len(audits) == 1 and SECRET not in audits[0].detail and row.ciphertext not in audits[0].detail
        await login(client, "editor")
        assert (await client.get(path)).json() == item
        monkeypatch.setattr(settings, "KNOWLEDGE_KEY_FILE", "")
        assert (await client.get(path)).json()["state"] == "unavailable"
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("overrides", [
    {"secret": "********"}, {"secret": "cookie: session=value"}, {"secret": "session=value\r\nX-Evil: 1"},
    {"secret": "session=中文"}, {"secret": "session=value "}, {"secret": "x" * 8193},
    {"secret": "Bearer private-token", "kind": "bearer"}, {"secret": "token with spaces", "kind": "bearer"},
    {"secret": "secret-without-cookie-name"}, {"headers": {"Authorization": "synthetic-do-not-echo"}},
    {"expected_credential_version": -1},
])
def test_invalid_secret_fields_do_not_echo_or_write(monkeypatch, tmp_path, overrides):
    """占位符、控制字符、任意请求头和错误并发字段在写入前安全拒绝。"""
    async def scenario(client, sessions, _redis):
        """响应只给固定验证说明，不把误填认证值反射到错误消息。"""
        headers = await login(client)
        origin = await source(client, headers)
        keys(monkeypatch, tmp_path)
        result = await client.put(ROOT + "/sources/" + origin["id"] + "/credential", headers=headers, json={
            "expected_source_version": 1, "expected_credential_version": 0, "kind": "cookie", "secret": SECRET, **overrides,
        })
        assert result.status_code == 422 and SECRET not in result.text and "synthetic-do-not-echo" not in result.text
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeCredential)) == 0
    run_scenario(monkeypatch, scenario)


def test_bound_target_cannot_be_repointed_and_clear_requires_current_versions(monkeypatch, tmp_path):
    """绑定后即使没有文档也禁止改目标，正常名称更新不使现有密文失效。"""
    async def scenario(client, sessions, _redis):
        """普通管理员不能借 URL 或路径编辑把秘密带往新目标，清除后方可维护。"""
        headers = await login(client)
        origin = await source(client, headers)
        keys(monkeypatch, tmp_path)
        path = ROOT + "/sources/" + origin["id"]
        saved = (await replace(client, headers, origin)).json()
        origin["version"] = saved["source_version"]
        editor = await login(client, "editor")
        for changes in ({"url": "https://attacker.example/rules"}, {"paths": ["logout"]},
                        {"selector": "#private"}, {"branch": "elsewhere"}):
            result = await client.put(path, headers=editor, json={
                **{key: origin[key] for key in FIELDS}, "expected_version": origin["version"], **changes,
            })
            assert result.status_code == 409 and SECRET not in result.text
        renamed = await client.put(path, headers=editor, json={
            **{key: origin[key] for key in FIELDS}, "name": "更清楚的名称", "expected_version": origin["version"],
        })
        assert renamed.status_code == 200
        async with sessions() as db:
            assert (await catalog.material(db, await db.get(KnowledgeSource, origin["id"]))).value.get_secret_value() == SECRET
        headers = await login(client)
        stale = await client.post(path + "/credential/clear", headers=headers, json=action(saved))
        assert stale.status_code == 409
        state = (await client.get(path + "/credential")).json()
        cleared = await client.post(path + "/credential/clear", headers=headers, json=action(state))
        assert cleared.status_code == 200 and cleared.json()["configured"] is False
        updated = await client.put(path, headers=headers, json={
            **{key: renamed.json()[key] for key in FIELDS}, "url": "https://different.example/rules",
            "expected_version": cleared.json()["source_version"],
        })
        assert updated.status_code == 200
        async with sessions() as db:
            assert await catalog.material(db, await db.get(KnowledgeSource, origin["id"])) is None
    run_scenario(monkeypatch, scenario)


def test_rewrap_uses_new_nonce_and_active_key_and_rejects_tampering(monkeypatch, tmp_path):
    """密文绑定来源、类型、版本与范围；轮换部署密钥时无须返回或重新提交认证值。"""
    async def scenario(client, sessions, _redis):
        """旧密钥保留到 rewrap 成功后再移除，重放旧版本请求不会覆盖新密文。"""
        headers = await login(client)
        origin = await source(client, headers)
        keyfile = keys(monkeypatch, tmp_path)
        state = (await replace(client, headers, origin)).json()
        async with sessions() as db:
            row = await db.scalar(select(KnowledgeCredential))
            original = {key: getattr(row, key) for key in ("source_id", "kind", "binding", "version", "nonce", "ciphertext", "key_id")}
            for change in ({"source_id": "f" * 32}, {"kind": "bearer"}, {"version": 2}, {"binding": "f" * 64},
                           {"nonce": "invalid"}, {"ciphertext": "invalid"}, {"key_id": "missing"}):
                with pytest.raises(HTTPException) as error:
                    cipher.decrypt(SimpleNamespace(**{**original, **change}))
                assert error.value.status_code == 503 and SECRET not in str(error.value)
        keys(monkeypatch, tmp_path, active="second", names=("first", "second"))
        path = ROOT + "/sources/" + origin["id"] + "/credential"
        result = await client.post(path + "/rewrap", headers=headers, json=action(state))
        assert result.status_code == 200 and result.json()["version"] == 2
        assert SECRET not in result.text
        data = json.loads(keyfile.read_text())
        del data["keys"]["first"]
        keyfile.write_text(json.dumps(data))
        async with sessions() as db:
            row = await db.scalar(select(KnowledgeCredential))
            assert row.key_id == "second" and row.nonce != original["nonce"] and cipher.decrypt(row) == SECRET
            with pytest.raises(HTTPException):
                cipher.decrypt(SimpleNamespace(**original))
        assert (await client.post(path + "/rewrap", headers=headers, json=action(state))).status_code == 409
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("fault", ["permissions", "symlink", "invalid", "oversize", "fifo", "repository"])
def test_deployment_key_file_must_be_private_valid_and_outside_repository(monkeypatch, tmp_path, fault):
    """不读取宽权限、符号链接、损坏、大文件、FIFO 或仓库内的密钥文件。"""
    location = keys(monkeypatch, tmp_path)
    if fault == "permissions":
        location.chmod(0o644)
    elif fault == "symlink":
        link = tmp_path / "link"
        link.symlink_to(location)
        monkeypatch.setattr(settings, "KNOWLEDGE_KEY_FILE", str(link))
    elif fault == "invalid":
        location.write_text('{"active":"wrong","keys":{}}')
    elif fault == "oversize":
        location.write_bytes(b"x" * 16385)
    elif fault == "fifo":
        import os
        location.unlink()
        os.mkfifo(location, mode=0o600)
    else:
        (tmp_path / ".git").mkdir()
    with pytest.raises(HTTPException) as error:
        cipher.keyring()
    assert error.value.status_code == 503 and str(location) not in str(error.value)


@pytest.mark.parametrize("connector", ["manual", "github"])
def test_only_supported_secret_kind_can_bind_to_network_source(monkeypatch, tmp_path, connector):
    """手工 / 导入不绑定出站秘密，GitHub 仅接受对应 API Bearer。"""
    async def scenario(client, sessions, _redis):
        """被拒绝的类型不产生密文，公开或手工来源也不要求部署密钥。"""
        headers = await login(client)
        origin = await source(client, headers, connector=connector)
        keys(monkeypatch, tmp_path)
        rejected = await replace(client, headers, origin)
        assert rejected.status_code == 422
        if connector == "github":
            result = await replace(client, headers, origin, kind="bearer", secret="synthetic-token-123")
            assert result.status_code == 200 and result.json()["host"] == "api.github.com"
        async with sessions() as db:
            count = await db.scalar(select(func.count()).select_from(KnowledgeCredential))
            assert count == int(connector == "github")
    run_scenario(monkeypatch, scenario)


def test_two_real_connections_cannot_overwrite_credential_or_target(monkeypatch, tmp_path):
    """双连接同时替换只有一个版本成功；来源编辑与凭据写入也共享并发边界。"""
    async def scenario(client, sessions, _redis):
        """实际 HTTP 请求使用两个 SQL 连接，结果不能混合新目标和旧授权秘密。"""
        headers = await login(client)
        origin = await source(client, headers)
        keys(monkeypatch, tmp_path)
        results = await asyncio.gather(
            replace(client, headers, origin, secret="session=synthetic-one"),
            replace(client, headers, origin, secret="session=synthetic-two"),
        )
        assert sorted(result.status_code for result in results) == [200, 409]
        current = next(result.json() for result in results if result.status_code == 200)
        async with sessions() as db:
            rows = (await db.scalars(select(KnowledgeCredential))).all()
            assert len(rows) == 1 and cipher.decrypt(rows[0]) in {"session=synthetic-one", "session=synthetic-two"}
        path = ROOT + "/sources/" + origin["id"]
        cleared = (await client.post(path + "/credential/clear", headers=headers, json=action(current))).json()
        origin["version"] = cleared["source_version"]
        mutation = {**{key: origin[key] for key in FIELDS}, "url": "https://new.example/rules",
                    "expected_version": origin["version"]}
        results = await asyncio.gather(replace(client, headers, origin),
                                       client.put(path, headers=headers, json=mutation))
        assert sorted(result.status_code for result in results) == [200, 409]
        async with sessions() as db:
            actual = await db.get(KnowledgeSource, origin["id"])
            row = await catalog.credential(db, origin["id"])
            if row:
                assert actual.url == origin["url"] and row.binding == policy.binding(actual)
            else:
                assert actual.url == "https://new.example/rules"
    run_scenario(monkeypatch, scenario, database_url="sqlite+aiosqlite:///" + str(tmp_path / "connections.db"))
