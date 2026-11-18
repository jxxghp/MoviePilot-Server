"""
手工批准、撤回、持久化执行器与真实私有 FTS5 快照的隔离回归。
"""

import contextlib
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.models.admin import AdminAudit
from app.models.knowledge import KnowledgeDocument, KnowledgeJob, KnowledgeState
from app.services.knowledge import indexes, jobs
from app.services.knowledge.worker import KnowledgeWorker
from test_knowledge_catalog import ROOT, collection, login, run_scenario


async def draft(client, headers, *, source=None, external_id="guide", content="分享率规则说明 DOWNLOAD_PATH"):
    """
    建立合成手工来源与未发布文档，不导入附件或抓取网络。
    """
    if source is None:
        parent = await collection(client, headers)
        response = await client.post(ROOT + "/sources", headers=headers, json={
            "name": "合成手工来源", "collection_id": parent["id"], "connector": "manual", "enabled": True,
        })
        assert response.status_code == 201, response.text
        source = response.json()
    response = await client.post(ROOT + "/documents", headers=headers, json={
        "source_id": source["id"], "external_id": external_id, "title": "合成指南 " + external_id, "content": content,
    })
    assert response.status_code == 201, response.text
    return source, response.json()


async def publish(client, headers, document, number=None):
    """
    明确指定所审阅的修订及文档版本，返回真实排队响应。
    """
    response = await client.post(ROOT + "/documents/" + document["id"] + "/publish", headers=headers, json={
        "expected_version": document["version"], "revision": number or document["head_revision"],
    })
    assert response.status_code == 200, response.text
    return response.json()


async def current_state(sessions):
    """
    从事实数据库重新读取索引指针，不依赖执行器返回的乐观结论。
    """
    async with sessions() as db:
        return await db.get(KnowledgeState, "main")


def snapshot_rows(directory, state, query=None):
    """
    打开真实不可变 FTS5 文件读取片段或匹配行，随后关闭文件句柄。
    """
    path = directory / (state.active_snapshot + ".sqlite")
    assert path.stat().st_mode & 0o777 == 0o400
    with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        assert metadata["complete"] == "1" and int(metadata["generation"]) == state.active_generation
        if query is not None:
            return db.execute("SELECT c.document_id, c.content FROM search s JOIN chunks c ON c.id=s.rowid "
                              "WHERE search MATCH ?", (query,)).fetchall()
        return db.execute("SELECT document_id, revision_number, content FROM chunks").fetchall()


def test_publish_index_draft_history_and_withdraw(monkeypatch, tmp_path):
    """
    后台批准仅排队，独立执行器只索引发布版；追加草稿与撤回均保留不可变历史。
    """
    async def scenario(client, sessions, _redis):
        """
        用真实中文和配置键 FTS 查询验证文件内容，而不只核对生成了文件名。
        """
        headers = await login(client)
        source, document = await draft(client, headers)
        path = ROOT + "/documents/" + document["id"]
        assert (await client.get(ROOT + "/jobs")).json()["total"] == 0
        assert (await client.post(path + "/publish", json={
            "expected_version": 1, "revision": 1,
        })).status_code == 403
        published = await publish(client, headers, document)
        assert published["index_pending"] and published["job"]["status"] == "queued"
        assert published["document"]["published_revision"] == 1
        assert "content" not in published["document"]["revision"]
        directory = indexes.private_directory(str(tmp_path / "private"))
        worker = KnowledgeWorker(sessions, directory)
        assert await worker.run_once() is None
        assert not (await current_state(sessions)).active_snapshot
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        assert (await worker.run_once())["outcome"] == "activated"
        first = await current_state(sessions)
        assert first.generation == first.active_generation == 1
        assert snapshot_rows(directory, first) == [(document["id"], 1, document["revision"]["content"])]
        assert snapshot_rows(directory, first, '"分享"') and snapshot_rows(directory, first, '"download_path"')
        revised = await client.post(path + "/revisions", headers=headers, json={
            "expected_version": published["document"]["version"], "title": "待审新修订", "content": "这是新草稿",
        })
        assert revised.status_code == 201
        assert revised.json()["published_revision"] == 1 and revised.json()["head_revision"] == 2
        assert await worker.run_once() is None
        assert snapshot_rows(directory, first)[0][2] != "这是新草稿"
        unchanged = await publish(client, headers, revised.json(), 1)
        assert unchanged["job"] is None and unchanged["index_pending"] is False
        second = await publish(client, headers, revised.json(), 2)
        repeated = await publish(client, headers, second["document"], 2)
        assert repeated["job"] is None and repeated["index_pending"] is True
        withdrawn = await client.post(path + "/withdraw", headers=headers, json={
            "expected_version": second["document"]["version"],
        })
        assert withdrawn.status_code == 200 and withdrawn.json()["document"]["published_revision"] is None
        assert withdrawn.json()["job"]["id"] == second["job"]["id"]
        async with sessions() as db:
            assert (await db.get(KnowledgeDocument, document["id"])).published_revision is None
        assert (await client.get(path, params={"revision": 1})).json()["revision"]["state"] == "superseded"
        assert (await client.get(path, params={"revision": 2})).json()["revision"]["state"] == "withdrawn"
        assert (await worker.run_once())["outcome"] == "activated"
        final = await current_state(sessions)
        assert final.generation == final.active_generation == 3
        assert snapshot_rows(directory, final) == []
        assert (directory / (first.active_snapshot + ".sqlite")).exists()
        listed = await client.get(ROOT + "/jobs")
        assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
        assert "lease_token" not in listed.text and str(directory) not in listed.text
        status = (await client.get(ROOT + "/status")).json()
        assert status["index"] == {"generation": 3, "active_generation": 3, "has_snapshot": True}
    run_scenario(monkeypatch, scenario)


def test_publication_requires_current_version_explicit_revision_and_enabled_source(monkeypatch):
    """
    不能误发并发新草稿、跨文档修订或停用来源，普通管理员可按既有内容职责发布。
    """
    async def scenario(client, sessions, _redis):
        """
        各失败路径不改发布指针、不建任务，也不写成功审计。
        """
        headers = await login(client)
        source, document = await draft(client, headers)
        path = ROOT + "/documents/" + document["id"]
        for payload, code in (
            ({"expected_version": 2, "revision": 1}, 409),
            ({"expected_version": 1, "revision": 99}, 404),
            ({"expected_version": 1}, 422),
        ):
            response = await client.post(path + "/publish", headers=headers, json=payload)
            assert response.status_code == code
        async with sessions() as db:
            assert (await db.get(KnowledgeDocument, document["id"])).published_revision is None
            assert not (await db.scalars(select(KnowledgeJob))).all()
        source_fields = ("collection_id", "site_id", "name", "connector", "url", "repository",
                         "branch", "paths", "selector", "refresh_minutes", "enabled")
        disabled = await client.put(ROOT + "/sources/" + source["id"], headers=headers, json={
            **{key: source[key] for key in source_fields}, "enabled": False, "expected_version": source["version"],
        })
        assert disabled.status_code == 200
        assert (await client.post(path + "/publish", headers=headers, json={
            "expected_version": 1, "revision": 1,
        })).status_code == 409
        enabled = await client.put(ROOT + "/sources/" + source["id"], headers=headers, json={
            **{key: disabled.json()[key] for key in source_fields}, "enabled": True,
            "expected_version": disabled.json()["version"],
        })
        assert enabled.status_code == 200
        editor = await login(client, "editor")
        approved = await publish(client, editor, document)
        assert approved["document"]["published_revision"] == 1
        async with sessions() as db:
            audits = (await db.scalars(select(AdminAudit).where(AdminAudit.action == "knowledge.document.publish"))).all()
            assert len(audits) == 1 and audits[0].actor == "editor"
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("kind", ["collections", "sources", "sites"])
def test_switches_queue_rebuild_and_reenable_restores_published_only(monkeypatch, tmp_path, kind):
    """
    站点、集合及来源开关的数据库变更会重建快照，重新启用不会带回未发布草稿。
    """
    async def scenario(client, sessions, _redis):
        """
        每种开关走真实后台表单和真实 worker，不直接伪造索引过滤结果。
        """
        headers = await login(client)
        parent = await collection(client, headers)
        site_response = await client.post(ROOT + "/sites", headers=headers, json={
            "name": "合成站点", "domain": "tracker.example", "aliases": ["合成别名"],
        })
        assert site_response.status_code == 201
        site = site_response.json()
        source_response = await client.post(ROOT + "/sources", headers=headers, json={
            "name": "本站规则", "collection_id": parent["id"], "site_id": site["id"],
            "connector": "manual", "enabled": True,
        })
        source = source_response.json()
        _source, document = await draft(client, headers, source=source)
        await draft(client, headers, source=source, external_id="unreviewed", content="不能进入索引的草稿")
        await publish(client, headers, document)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        worker = KnowledgeWorker(sessions, directory)
        await worker.run_once()
        assert len(snapshot_rows(directory, await current_state(sessions))) == 1
        item, fields = {
            "collections": (parent, ("slug", "name", "description", "domain", "product", "enabled")),
            "sites": (site, ("name", "domain", "aliases", "enabled")),
            "sources": (source, ("collection_id", "site_id", "name", "connector", "url", "repository",
                                 "branch", "paths", "selector", "refresh_minutes", "enabled")),
        }[kind]
        for enabled, count in ((False, 0), (True, 1)):
            response = await client.put(ROOT + "/" + kind + "/" + item["id"], headers=headers, json={
                **{key: item[key] for key in fields}, "enabled": enabled, "expected_version": item["version"],
            })
            assert response.status_code == 200, response.text
            item = response.json()
            assert (await worker.run_once())["outcome"] == "activated"
            state = await current_state(sessions)
            assert state.active_generation == state.generation
            assert len(snapshot_rows(directory, state)) == count
    run_scenario(monkeypatch, scenario)


def test_generation_change_during_build_never_activates_stale_snapshot(monkeypatch, tmp_path):
    """
    构建期间的新发布合并到活动任务后，完成者不切换旧快照，而是补排下一代。
    """
    async def scenario(client, sessions, _redis):
        """
        在真实文件构建完成与 SQL 激活之间插入第二次发布，验证后续任务没有丢失。
        """
        headers = await login(client)
        source, document = await draft(client, headers)
        published = await publish(client, headers, document)
        directory = indexes.private_directory(str(tmp_path / "private"))
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        original = indexes.build_snapshot

        async def changed(*args):
            """
            第一次文件构建后由另一个真实管理请求推进知识事实代次。
            """
            builder = await original(*args)
            _source, addition = await draft(client, headers, source=source, external_id="addition")
            another = await publish(client, headers, addition)
            assert another["job"]["id"] == published["job"]["id"]
            return builder

        monkeypatch.setattr(indexes, "build_snapshot", changed)
        worker = KnowledgeWorker(sessions, directory)
        assert (await worker.run_once())["outcome"] == "superseded"
        assert not (await current_state(sessions)).active_snapshot
        assert not list(directory.iterdir())
        queued = (await client.get(ROOT + "/jobs")).json()["items"]
        assert sorted(item["status"] for item in queued) == ["queued", "succeeded"]
        assert any(item["outcome"] == "superseded" for item in queued)
        monkeypatch.setattr(indexes, "build_snapshot", original)
        assert (await worker.run_once())["outcome"] == "activated"
        state = await current_state(sessions)
        assert state.generation == state.active_generation == 2
        assert len(snapshot_rows(directory, state)) == 2
    run_scenario(monkeypatch, scenario)


def test_running_cancel_prevents_activation_and_cleans_own_file(monkeypatch, tmp_path):
    """
    正在构建的任务收到取消后不能激活；只清理自己的未上线文件。
    """
    async def scenario(client, sessions, _redis):
        """
        在实际构建结束后取消，覆盖最接近激活的竞态窗口。
        """
        headers = await login(client)
        _source, document = await draft(client, headers)
        published = await publish(client, headers, document)
        original = indexes.build_snapshot

        async def cancelled(*args):
            """
            用真实取消 API 设置当前运行任务的取消请求。
            """
            builder = await original(*args)
            result = await client.post(ROOT + "/jobs/" + published["job"]["id"] + "/cancel",
                                       headers=headers, json={"expected_version": 2})
            assert result.status_code == 200 and result.json()["cancel_requested"]
            return builder

        monkeypatch.setattr(indexes, "build_snapshot", cancelled)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        assert (await KnowledgeWorker(sessions, directory).run_once())["outcome"] == "cancelled"
        assert not (await current_state(sessions)).active_snapshot and not list(directory.iterdir())
        items = (await client.get(ROOT + "/jobs")).json()["items"]
        assert len(items) == 1 and items[0]["status"] == "cancelled"
    run_scenario(monkeypatch, scenario)


def test_activation_lost_commit_ack_keeps_possible_online_file(monkeypatch, tmp_path):
    """
    激活事务已经提交但回执丢失时，执行器不能删除数据库可能已经引用的完整快照。
    """
    async def scenario(client, sessions, _redis):
        """
        让真实事务先成功提交再模拟连接异常，以数据库和文件共同验证最终状态。
        """
        headers = await login(client)
        _source, document = await draft(client, headers)
        await publish(client, headers, document)
        original = jobs.finish_index

        async def lost_ack(db, *args):
            """
            模拟提交成功后连接断开，不仅是在提交前抛一个异常。
            """
            await original(db, *args)
            await db.commit()
            raise SQLAlchemyError("synthetic-private-content-must-not-be-recorded")

        monkeypatch.setattr(jobs, "finish_index", lost_ack)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        result = await KnowledgeWorker(sessions, directory).run_once()
        assert result["outcome"] == "lease_lost"
        state = await current_state(sessions)
        assert state.active_snapshot and snapshot_rows(directory, state)
        async with sessions() as db:
            row = await db.get(KnowledgeJob, result["job_id"])
            assert row.status == "succeeded" and row.outcome == "activated"
            audits = (await db.scalars(select(AdminAudit))).all()
            assert "synthetic-private-content" not in json.dumps([row.detail for row in audits])
    run_scenario(monkeypatch, scenario)


def test_chunk_offsets_and_private_storage_boundaries(tmp_path):
    """
    长文切分可回溯且有大小上限，运行期目录不能落入源码或共享临时目录。
    """
    content = " \n第一段\n\n" + ("下载设置 DOWNLOAD_PATH 中文说明。" * 400) + "\n  "
    parts = list(indexes.chunks(content))
    assert len(parts) > 2
    assert all(content[start:end] == body and 0 < len(body) <= indexes.CHUNK_SIZE for start, end, body in parts)
    assert all(parts[index + 1][0] <= parts[index][1] for index in range(len(parts) - 1))
    assert "download_path" in indexes.tokens(content) and "中文" in indexes.tokens(content)
    repository = Path(__file__).resolve().parents[1]
    for configured in ("", "relative", "/", "/tmp/private-knowledge", str(repository / ".artifacts/private")):
        with pytest.raises(ValueError):
            indexes.private_directory(configured)
    fake_repo = tmp_path / "another-repository"
    (fake_repo / ".git").mkdir(parents=True)
    with pytest.raises(ValueError):
        indexes.private_directory(str(fake_repo / "private"))
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(ValueError):
        indexes.private_directory(str(shared))


def test_separate_worker_process_consumes_durable_queue(monkeypatch, tmp_path):
    """
    真正启动独立 Python worker，证明任务不依赖 Web 内存、测试回调或 Redis 会话。
    """
    async def scenario(client, sessions, _redis):
        """
        父进程只通过后台建数据，子进程仅凭同一隔离 SQL 与私有卷完成索引。
        """
        headers = await login(client)
        _source, document = await draft(client, headers)
        await publish(client, headers, document)
        data = tmp_path / "worker-private"
        environment = {**os.environ, "DATABASE_TYPE": "sqlite", "CONFIG_DIR": str(tmp_path),
                       "KNOWLEDGE_DATA_DIR": str(data), "KNOWLEDGE_ENABLED": "true", "DEBUG": "false"}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.services.knowledge.worker", "--once",
            cwd=Path(__file__).resolve().parents[1], env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 0, stderr.decode()
        assert "activated" in stderr.decode() and document["revision"]["content"].encode() not in stdout + stderr
        state = await current_state(sessions)
        assert state.active_snapshot and state.generation == state.active_generation == 1
        assert snapshot_rows(data / "indexes", state)[0][0] == document["id"]
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}")


def test_concurrent_publish_and_withdraw_change_one_generation(monkeypatch, tmp_path):
    """
    相同文档版本的发布和撤回竞争只能成功一次，修订状态与知识代次不能分别覆盖。
    """
    async def scenario(client, sessions, _redis):
        """
        两个真实连接修改同一发布身份，最终索引必须与 SQL 的实际赢家一致。
        """
        headers = await login(client)
        _source, document = await draft(client, headers)
        approved = await publish(client, headers, document)
        path = ROOT + "/documents/" + document["id"]
        revised = await client.post(path + "/revisions", headers=headers, json={
            "expected_version": approved["document"]["version"], "title": "第二版", "content": "合成第二版",
        })
        assert revised.status_code == 201
        version = revised.json()["version"]
        published, withdrawn = await asyncio.gather(
            client.post(path + "/publish", headers=headers, json={"expected_version": version, "revision": 2}),
            client.post(path + "/withdraw", headers=headers, json={"expected_version": version}),
        )
        assert sorted([published.status_code, withdrawn.status_code]) == [200, 409]
        current = (await client.get(path)).json()
        assert current["version"] == version + 1
        assert current["published_revision"] == (2 if published.status_code == 200 else None)
        previous = (await client.get(path, params={"revision": 1})).json()["revision"]
        assert previous["state"] == ("superseded" if published.status_code == 200 else "withdrawn")
        assert (await current_state(sessions)).generation == 2
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        assert (await KnowledgeWorker(sessions, directory).run_once())["outcome"] == "activated"
        rows = snapshot_rows(directory, await current_state(sessions))
        assert rows == ([(document["id"], 2, "合成第二版")] if published.status_code == 200 else [])
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'publish-race.db'}")
