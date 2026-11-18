"""
中心知识实际只读协议、范围、撤回和短期引用合同；全部使用合成内容与隔离资源。
"""

import asyncio
import os
import re
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from redis.exceptions import ConnectionError
from sqlalchemy import select

from app.api.knowledge import router
from app.core.config import settings
from app.models.knowledge import KnowledgeJob, KnowledgeState
from app.schemas.knowledge import KnowledgeSearch
from app.services.knowledge import indexes
from app.services.knowledge.retrieval import queries, session, storage
from app.services.knowledge.worker import KnowledgeWorker
from test_knowledge_access import CLIENTS, bearer, issue, update_payload
from test_knowledge_catalog import ROOT, collection, login, run_scenario as catalog_scenario
from test_knowledge_index import current_state, publish

API = "/knowledge/v1"


def run_scenario(monkeypatch, scenario, **kwargs):
    """复用真实后台会话、CSRF 和独立 Bearer，不替换产品授权依赖。"""
    return catalog_scenario(monkeypatch, scenario, extra_router=router, **kwargs)


async def source(client, headers, parent, *, site=None, name="合成知识"):
    """建立明确集合、可选站点的手工来源，不请求网络。"""
    result = await client.post(ROOT + "/sources", headers=headers, json={
        "name": name, "collection_id": parent["id"], "site_id": site["id"] if site else None,
        "connector": "manual", "enabled": True,
    })
    assert result.status_code == 201, result.text
    return result.json()


async def article(client, headers, origin, *, external_id="guide", content="分享率规则 DOWNLOAD_PATH",
                  title="合成指南", **metadata):
    """通过实际维护接口新增并明确批准一个合成修订。"""
    result = await client.post(ROOT + "/documents", headers=headers, json={
        "source_id": origin["id"], "external_id": external_id, "title": title, "content": content, **metadata,
    })
    assert result.status_code == 201, result.text
    return (await publish(client, headers, result.json()))["document"]


async def ready(monkeypatch, tmp_path, sessions):
    """使用真正独立执行器生成可检索的 FTS5 文件，配置只指向测试私有目录。"""
    directory = indexes.private_directory(str(tmp_path / "private"))
    monkeypatch.setattr(settings, "KNOWLEDGE_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    worker = KnowledgeWorker(sessions, directory)
    while await worker.run_once() is not None:
        pass
    return worker, directory


async def search(client, token, *, query="分享率", **scope):
    """发出宿主提供产品和当前版本的真实只读请求。"""
    return await client.post(API + "/search", headers=bearer(token), json={
        "protocol_version": 1, "query": query, "product": "moviepilot", "product_version": "3.2.5", **scope,
    })


async def read(client, token, identifier):
    """仅使用不透明短期引用补读，不传任意文档身份或偏移。"""
    return await client.post(API + "/read", headers=bearer(token), json={
        "protocol_version": 1, "evidence_id": identifier,
    })


def test_search_read_bounded_evidence_and_metadata_only_references(monkeypatch, tmp_path):
    """中文、配置键、补读、跨客户端引用和 Redis 最小留存通过实际接口验证。"""
    async def scenario(client, sessions, redis):
        """使用长文档验证搜索去重与补读，检查 Redis 未保存查询、标题或正文。"""
        headers = await login(client)
        parent = await collection(client, headers)
        origin = await source(client, headers, parent)
        content = "资料开头。" * 100 + "本段分享率与 DOWNLOAD_PATH 设置说明。" + "补充说明。" * 500
        doc = await article(client, headers, origin, content=content, title="不能缓存的合成标题",
                            source_url="https://docs.example/guide#rule")
        issued = await issue(client, headers, [parent["id"]])
        other = await issue(client, headers, [parent["id"]])
        await ready(monkeypatch, tmp_path, sessions)
        result = await search(client, issued["token"], query="如何设置 DOWNLOAD_PATH 分享率")
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["status"] == "matched" and len(data["evidence"]) == 1
        assert data["retention"] == "ephemeral" and data["content_trust"] == "reference_only"
        item = data["evidence"][0]
        assert item["document_id"] == doc["id"] and item["revision"] == 1
        assert item["source_url"] == "https://docs.example/guide#rule"
        assert len(item["excerpt"]) <= 1600 and item["freshness"] == "unknown" and item["stale"] is None
        assert {"applicability_unknown", "fetched_time_unknown"} <= set(data["notices"])
        identifier = item["evidence_id"]
        assert re.fullmatch(r"ke_[A-Za-z0-9_-]{43}", identifier)
        keys = await redis.keys("knowledge:*")
        assert len([key for key in keys if key.startswith("knowledge:reference:")]) == 1
        for key in keys:
            value = str(await redis.get(key))
            assert content not in value and "不能缓存" not in value and "如何设置" not in value
            assert identifier not in key and issued["token"] not in key + value
        assert 0 < await redis.ttl(session.reference_key(identifier)) <= 600
        expanded = await read(client, issued["token"], identifier)
        assert expanded.status_code == 200 and expanded.json()["status"] == "matched"
        assert len(expanded.json()["evidence"][0]["excerpt"]) > len(item["excerpt"])
        assert len(expanded.json()["evidence"][0]["excerpt"]) <= 4000
        forbidden = await read(client, other["token"], identifier)
        assert forbidden.status_code == 410 and "excerpt" not in forbidden.text
        await redis.delete(session.reference_key(identifier))
        expired = await read(client, issued["token"], identifier)
        assert expired.status_code == 410
        for response in (result, expanded, forbidden, expired):
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["vary"] == "Authorization"
    run_scenario(monkeypatch, scenario)


def test_product_version_site_and_content_kind_are_filtered(monkeypatch, tmp_path):
    """不同产品、版本和站点不能串用；普通使用问答不被源码测试占满。"""
    async def scenario(client, sessions, _redis):
        """所有范围共用一个真实快照，逐项断言筛选后的证据身份与状态。"""
        headers = await login(client)
        parent = await collection(client, headers, domain="pt", product="")
        origin = await source(client, headers, parent)
        general = await article(client, headers, origin, external_id="general", version_scope="all")
        valid = await article(client, headers, origin, external_id="valid", content="版本专用术语 versionneedle",
                              product="moviepilot", version_scope="range", min_version="3.2", max_version="3.9")
        await article(client, headers, origin, external_id="old", content="旧版独有术语 legacyneedle",
                      product="moviepilot", version_scope="range", max_version="2.9")
        await article(client, headers, origin, external_id="expired", content="过期独有术语 expiredneedle",
                      fetched_at="2020-01-01T00:00:00Z", valid_until="2021-01-01T00:00:00Z")
        await article(client, headers, origin, external_id="other-product", product="another-product",
                      content="别的产品独有说明 alienproduct")
        await article(client, headers, origin, external_id="tests/test_subscription.py",
                      content="测试专用术语 testingneedle 分享率", title="源码测试")
        site = (await client.post(ROOT + "/sites", headers=headers, json={
            "name": "甲站", "domain": "alpha.example", "aliases": ["甲站别名"],
        })).json()
        site_source = await source(client, headers, parent, site=site)
        specific = await article(client, headers, site_source, content="分享率 甲站独有规则")
        issued = await issue(client, headers, [parent["id"]])
        await ready(monkeypatch, tmp_path, sessions)
        token = issued["token"]
        general_result = (await search(client, token, domain="pt")).json()
        assert [item["document_id"] for item in general_result["evidence"]] == [general["id"]]
        assert "site_unspecified" in general_result["notices"]
        scoped = (await search(client, token, domain="pt", site="甲站别名")).json()
        assert {item["document_id"] for item in scoped["evidence"]} == {general["id"], specific["id"]}
        assert (await search(client, token, query="versionneedle", domain="pt")).json()["evidence"][0]["document_id"] == valid["id"]
        for query, version, notice in (
            ("versionneedle", None, "product_version_required"), ("legacyneedle", "3.2.5", "version_not_applicable"),
            ("expiredneedle", "3.2.5", "knowledge_expired"),
        ):
            data = (await search(client, token, query=query, product_version=version)).json()
            assert data["status"] == "not_applicable" and data["evidence"] == [], data
            assert notice in data["notices"]
        assert (await search(client, token, query="alienproduct")).json()["status"] == "no_match"
        assert (await search(client, token, query="testingneedle")).json()["status"] == "no_match"
        development = (await search(client, token, query="testingneedle", purpose="development")).json()
        assert development["evidence"][0]["kind"] == "test"
        assert "test_reference_only" in development["notices"]
        expanded = (await read(client, token, development["evidence"][0]["evidence_id"])).json()
        assert "test_reference_only" in expanded["notices"]
        unavailable = (await search(client, token, site="不在授权内的站点")).json()
        assert unavailable["status"] == "not_applicable" and unavailable["evidence"] == []
        assert (await search(client, token, product="another-product", query="alienproduct")).json()["status"] == "matched"
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("change", ["withdraw", "source", "site", "collection", "grant", "rotate"])
def test_stale_snapshot_and_reference_cannot_restore_revoked_content(monkeypatch, tmp_path, change):
    """撤回、停用、撤权与换令牌无须等待重新索引即可阻断后续读取。"""
    async def scenario(client, sessions, _redis):
        """保持旧快照原样不重建，再用原令牌和引用重复访问。"""
        headers = await login(client)
        parent = await collection(client, headers)
        site = (await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成站点", "domain": "scoped.example", "aliases": [],
        })).json()
        origin = await source(client, headers, parent, site=site)
        doc = await article(client, headers, origin, content="撤回标记 分享率")
        issued = await issue(client, headers, [parent["id"]])
        await ready(monkeypatch, tmp_path, sessions)
        before = await current_state(sessions)
        item = (await search(client, issued["token"], site="合成站点")).json()["evidence"][0]
        if change == "withdraw":
            mutation = await client.post(ROOT + "/documents/" + doc["id"] + "/withdraw", headers=headers,
                                         json={"expected_version": doc["version"]})
        elif change == "grant":
            mutation = await client.put(CLIENTS + "/" + issued["id"], headers=headers,
                                        json=update_payload(issued, collection_ids=[]))
        elif change == "rotate":
            mutation = await client.post(CLIENTS + "/" + issued["id"] + "/rotate", headers=headers,
                                         json={"expected_version": issued["version"]})
        else:
            value, fields = {
                "source": (origin, ("collection_id", "site_id", "name", "connector", "url", "repository",
                                    "branch", "paths", "selector", "refresh_minutes", "enabled")),
                "site": (site, ("name", "domain", "aliases", "enabled")),
                "collection": (parent, ("slug", "name", "description", "domain", "product", "enabled")),
            }[change]
            mutation = await client.put(ROOT + "/" + change + "s/" + value["id"], headers=headers, json={
                **{key: value[key] for key in fields}, "enabled": False, "expected_version": value["version"],
            })
        assert mutation.status_code == 200, mutation.text
        assert (await current_state(sessions)).active_snapshot == before.active_snapshot
        reread = await read(client, issued["token"], item["evidence_id"])
        assert reread.status_code in {401, 403, 410}, reread.text
        repeat = await search(client, issued["token"], site="合成站点")
        assert repeat.status_code in {200, 401, 403, 503}, repeat.text
        for response in (reread, repeat):
            assert "撤回标记" not in response.text and "excerpt" not in response.text
            assert response.headers["cache-control"] == "no-store"
    run_scenario(monkeypatch, scenario)


def test_current_revision_and_missing_index_have_distinct_failure_states(monkeypatch, tmp_path):
    """新修订不能借旧块位置返回内容；索引未就绪、格式过旧和真无匹配可区分。"""
    async def scenario(client, sessions, _redis):
        """在真实发布指针改变后检查旧引用，再重建恢复检索。"""
        headers = await login(client)
        parent = await collection(client, headers)
        origin = await source(client, headers, parent)
        doc = await article(client, headers, origin, content="原始分享率规则")
        issued = await issue(client, headers, [parent["id"]])
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        assert (await search(client, issued["token"])).status_code == 503
        worker, directory = await ready(monkeypatch, tmp_path, sessions)
        first = (await search(client, issued["token"])).json()["evidence"][0]
        updated = await client.post(ROOT + "/documents/" + doc["id"] + "/revisions", headers=headers, json={
            "expected_version": doc["version"], "title": "新版", "content": "新修订专用词",
        })
        assert updated.status_code == 201
        await publish(client, headers, updated.json())
        assert (await read(client, issued["token"], first["evidence_id"])).status_code == 410
        pending = await search(client, issued["token"], query="新修订专用")
        assert pending.status_code == 503 and pending.json()["detail"] == "knowledge_index_updating"
        assert (await worker.run_once())["outcome"] == "activated"
        new = await search(client, issued["token"], query="新修订专用")
        assert new.json()["status"] == "matched" and new.json()["evidence"][0]["revision"] == 2
        assert (await search(client, issued["token"], query="不存在的英文 abcxyz")).json()["status"] == "no_match"
        state = await current_state(sessions)
        path = directory / (state.active_snapshot + ".sqlite")
        path.chmod(0o600)
        with sqlite3.connect(path) as snapshot:
            snapshot.execute("UPDATE metadata SET value='1' WHERE key='format_version'")
        path.chmod(0o400)
        incompatible = await search(client, issued["token"], query="新修订专用")
        assert incompatible.status_code == 503 and "新修订专用词" not in incompatible.text
        path.unlink()
        missing = await search(client, issued["token"], query="新修订专用")
        assert missing.status_code == 503 and "evidence" not in missing.json()
    run_scenario(monkeypatch, scenario)


def test_validation_quotas_and_redis_failure_do_not_echo_input(monkeypatch, tmp_path):
    """有界请求、字面查询、共享配额、Redis 故障和无效凭据都有禁止缓存的明确状态。"""
    async def scenario(client, sessions, redis):
        """只修改限额常数缩短测试，不替换真实配额实现或认证依赖。"""
        headers = await login(client)
        parent = await collection(client, headers)
        origin = await source(client, headers, parent)
        await article(client, headers, origin)
        issued = await issue(client, headers, [parent["id"]])
        await ready(monkeypatch, tmp_path, sessions)
        invalid = [
            await search(client, issued["token"], query="token=synthetic-private-value-123456789"),
            await search(client, issued["token"], product_version="latest"),
            await client.post(API + "/read", headers=bearer(issued["token"]),
                              json={"protocol_version": 1, "document_id": "f" * 32}),
            await client.post(API + "/search", headers=bearer(issued["token"]), content=b"x" * 8193),
            await client.post(API + "/search", headers={**bearer(issued["token"]), "Content-Encoding": "gzip"},
                              content=b"compressed-not-supported"),
            await search(client, "mpa_" + "a" * 43),
        ]
        assert [item.status_code for item in invalid] == [422, 422, 422, 413, 415, 401]
        assert all("synthetic-private-value" not in result.text for result in invalid)
        for response in invalid:
            assert response.headers["cache-control"] == "no-store"
        literal = await search(client, issued["token"], query='DOWNLOAD_PATH" OR * NEAR(')
        assert literal.status_code == 200 and literal.json()["status"] == "matched"
        await redis.delete(*(await redis.keys("knowledge:rate:*")))
        monkeypatch.setattr(session, "CLIENT_PER_MINUTE", 1)
        assert (await search(client, issued["token"])).status_code == 200
        limited = await search(client, issued["token"])
        assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
        await redis.delete(*(await redis.keys("knowledge:rate:*")))
        monkeypatch.setattr(session, "ADDRESS_PER_MINUTE", 1)
        assert (await search(client, issued["token"])).status_code == 200
        assert (await search(client, "invalid")).status_code == 429

        def unavailable():
            """模拟真实 Redis 连接故障，不能退化到无配额模式。"""
            raise ConnectionError("must-not-echo-private-endpoint")

        monkeypatch.setattr(session, "redis", unavailable)
        down = await search(client, issued["token"])
        assert down.status_code == 503 and down.json()["status"] == "unavailable"
        assert "must-not-echo" not in down.text
    run_scenario(monkeypatch, scenario)


def test_source_variants_and_total_character_budget_are_explicit(monkeypatch, tmp_path):
    """同身份不同文本提示需核对，重复块去重且总正文预算受控。"""
    async def scenario(client, sessions, _redis):
        """用五个来源形成真正重复标题、外部身份和适用范围的异文。"""
        headers = await login(client)
        parent = await collection(client, headers)
        for number in range(5):
            origin = await source(client, headers, parent, name="来源" + str(number))
            await article(client, headers, origin, title="相同条目", content="分享率规则" * 500 + str(number),
                          version_scope="all")
        issued = await issue(client, headers, [parent["id"]])
        await ready(monkeypatch, tmp_path, sessions)
        data = (await search(client, issued["token"])).json()
        assert data["status"] == "conflicting" and "source_variants_need_review" in data["notices"]
        assert len(data["evidence"]) >= 2
        assert len({item["document_id"] for item in data["evidence"]}) == len(data["evidence"])
        assert sum(len(item["excerpt"]) for item in data["evidence"]) <= 6000
        assert all(len(item["excerpt"]) <= 1600 for item in data["evidence"])
    run_scenario(monkeypatch, scenario)


def test_cancelled_request_keeps_thread_capacity_until_actual_completion():
    """请求被取消后并发槽仍随真正线程结束释放，不能靠取消堆积工作线程。"""
    async def scenario():
        """阻塞四个真实线程，取消调用方后第五个请求仍被拒绝。"""
        gate = threading.Event()
        started = [threading.Event() for _ in range(storage.THREADS)]

        def work(marker):
            """模拟尚未退出的只读工作，完成后抛错也必须消费异常。"""
            marker.set()
            gate.wait(5)
            raise ValueError("synthetic worker failure")

        tasks = [asyncio.create_task(storage.bounded(work, marker)) for marker in started]
        try:
            assert await asyncio.to_thread(lambda: all(marker.wait(2) for marker in started))
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with pytest.raises(HTTPException) as error:
                await storage.bounded(lambda: 1)
            assert error.value.status_code == 503 and error.value.detail == "knowledge_busy"
        finally:
            gate.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(100):
            try:
                assert await storage.bounded(lambda: 7) == 7
                break
            except HTTPException:
                await asyncio.sleep(0.01)
        else:
            pytest.fail("真实线程结束后容量未恢复")
    asyncio.run(scenario())


def test_scope_filtering_precedes_candidate_limit_and_versions_are_numeric(tmp_path):
    """数百个高频但不适用片段不能挤掉正确版本，3.10 必须按数值而非字符串比较。"""
    directory = indexes.private_directory(str(tmp_path / "private"))
    builder = indexes.SnapshotBuilder(directory, 1)
    base = {
        "revision_number": 1, "collection_id": "a" * 32, "source_id": "b" * 32,
        "site_id": None, "title": "合成范围测试", "content": "versionneedle",
        "external_id": "guide", "source_url": "", "product": "moviepilot",
        "version_scope": "range", "min_version": "2.0.0", "max_version": "2.9.0", "valid_until": None,
    }
    try:
        for number in range(520):
            builder.add(SimpleNamespace(**base, document_id=format(number, "032x")))
        expected = "f" * 32
        builder.add(SimpleNamespace(**{**base, "min_version": "3.2.0", "max_version": "3.20.0"},
                                    document_id=expected))
        builder.finish()
        spec = KnowledgeSearch(protocol_version=1, query="versionneedle", product="moviepilot", product_version="3.10")
        candidates = storage.candidates(directory, builder.snapshot, queries.expression(spec.query),
                                        ["a" * 32], spec, None)
        assert [item["document_id"] for item in candidates] == [expected]
        assert all("content" not in item and "title" not in item for item in candidates)
    finally:
        builder.abort()


def test_snapshot_retirement_preserves_active_uncertain_and_short_references(monkeypatch, tmp_path):
    """退役保留当前及最新两份，活动构建保护不确定提交，补读不依赖旧文件。"""
    async def scenario(client, sessions, _redis):
        """只创建专属私有目录的合成快照，不清理未知文件或符号链接。"""
        headers = await login(client)
        parent = await collection(client, headers)
        origin = await source(client, headers, parent)
        await article(client, headers, origin)
        issued = await issue(client, headers, [parent["id"]])
        _worker, directory = await ready(monkeypatch, tmp_path, sessions)
        identifier = (await search(client, issued["token"])).json()["evidence"][0]["evidence_id"]
        active = (await current_state(sessions)).active_snapshot
        old = time.time() - 100
        paths = [directory / (str(number) * 32 + ".sqlite") for number in range(1, 5)]
        for offset, path in enumerate(paths):
            path.write_text("合成孤儿")
            os.utime(path, (old + offset, old + offset))
        # 当前文件虽最老也不能删除，最近两份可能是已完成但提交回执尚不明的文件。
        os.utime(directory / (active + ".sqlite"), (old - 10, old - 10))
        stranger = directory / "operator-backup.sqlite"
        stranger.write_text("非托管备份")
        link = directory / ("f" * 32 + ".sqlite")
        link.symlink_to(stranger)
        async with sessions() as db:
            assert await storage.collect(db, directory) == 2
        assert not paths[0].exists() and not paths[1].exists()
        assert all(path.exists() for path in paths[2:])
        assert stranger.exists() and link.is_symlink() and (directory / (active + ".sqlite")).exists()
        paths[0].write_text("运行中尚未决文件")
        os.utime(paths[0], (old, old))
        async with sessions() as db:
            job = KnowledgeJob(kind="index", status="running", lease_token="lease", lease_until=time.time() + 60,
                               available_at=time.time(), created_by="synthetic-test")
            db.add(job)
            await db.commit()
        async with sessions() as db:
            assert await storage.collect(db, directory) == 0
        assert paths[0].exists()
        # 模拟旧索引文件已退役；引用仍由 SQL 当前发布版和当前授权验证。
        (directory / (active + ".sqlite")).unlink()
        result = await read(client, issued["token"], identifier)
        assert result.status_code == 200 and result.json()["status"] == "matched"
        async with sessions() as db:
            assert (await db.scalar(select(KnowledgeState))).active_snapshot == active
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("query, expected, absent", [
    ("怎么设置 DOWNLOAD_PATH", "download_path", "怎么"),
    ("H&R 怎么算", "做种", None),
    ("chrome", "chrome", "做种"),
])
def test_query_terms_are_literals_and_synonyms_are_bounded(query, expected, absent):
    """词法归一保留配置键，英文别名不能误中其他单词内部。"""
    actual = queries.expression(query)
    assert expected in actual
    if absent:
        assert absent not in actual
