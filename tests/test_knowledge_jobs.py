"""
持久化索引队列的真实竞争、租约失联、取消、重试和终态回归。
"""

import asyncio
import time

import pytest
from sqlalchemy import create_mock_engine, select

from app.core.config import settings
from app.models import Base
from app.models.admin import AdminAudit
from app.models.knowledge import KnowledgeJob, KnowledgeJobSlot
from app.services.knowledge import indexes, jobs, worker as worker_module
from app.services.knowledge.worker import KnowledgeWorker
from test_knowledge_catalog import ROOT, login, run_scenario


async def request_index(client, headers):
    """
    通过真实后台接口排入重建，HTTP 202 明确表示尚未执行。
    """
    result = await client.post(ROOT + "/jobs/index", headers=headers, json={})
    assert result.status_code == 202, result.text
    assert result.json()["status"] in {"queued", "running"}
    return result.json()


def test_queue_requires_session_csrf_deduplicates_and_cancels(monkeypatch, tmp_path):
    """
    队列维护复用真实认证，同一个活动索引合并，取消后重新触发产生新任务。
    """
    async def scenario(client, sessions, _redis):
        """
        关闭服务可以排队准备，但不会运行；终态与冲突不暴露内部租约。
        """
        assert (await client.get(ROOT + "/jobs")).status_code == 401
        assert (await client.post(ROOT + "/jobs/index", json={})).status_code == 401
        headers = await login(client, "editor")
        assert (await client.post(ROOT + "/jobs/index", json={})).status_code == 403
        first = await request_index(client, headers)
        repeated = await request_index(client, headers)
        assert first["id"] == repeated["id"]
        directory = indexes.private_directory(str(tmp_path / "private"))
        assert await KnowledgeWorker(sessions, directory).run_once() is None
        cancelled = await client.post(ROOT + "/jobs/" + first["id"] + "/cancel",
                                      headers=headers, json={"expected_version": first["version"]})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["version"] == 2
        conflict = await client.post(ROOT + "/jobs/" + first["id"] + "/cancel",
                                     headers=headers, json={"expected_version": first["version"]})
        assert conflict.status_code == 409
        assert (await client.post(ROOT + "/jobs/" + "f" * 32 + "/cancel",
                                  headers=headers, json={"expected_version": 1})).status_code == 404
        second = await request_index(client, headers)
        assert first["id"] != second["id"]
        assert "lease_token" not in repeated
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        assert (await KnowledgeWorker(sessions, directory).run_once())["job_id"] == second["id"]
        items = (await client.get(ROOT + "/jobs")).json()["items"]
        assert sorted(item["status"] for item in items) == ["cancelled", "succeeded"]
    run_scenario(monkeypatch, scenario)


def test_two_real_connections_claim_one_lease_and_old_owner_is_fenced(monkeypatch, tmp_path):
    """
    两个真实连接竞争同一任务只认领一次；租约过期恢复后旧令牌不能续约或激活。
    """
    async def scenario(client, sessions, _redis):
        """
        使用数据库事实模拟时间推进，不用进程内锁或睡眠掩盖竞争。
        """
        headers = await login(client)
        queued = await request_index(client, headers)
        clock = time.time()

        async def contender():
            """
            每个竞争者使用独立连接，提交之后才把认领结果交给调用方。
            """
            async with sessions() as db:
                lease = await jobs.claim(db, now=clock)
                await db.commit()
                return lease

        results = await asyncio.gather(contender(), contender())
        assert sum(lease is not None for lease in results) == 1
        old = next(lease for lease in results if lease is not None)
        assert old.job_id == queued["id"]
        async with sessions() as db:
            assert await jobs.heartbeat(db, old, now=clock + jobs.LEASE_SECONDS - 1)
            await db.commit()
        recovered_time = clock + jobs.LEASE_SECONDS * 2
        async with sessions() as db:
            assert not await jobs.heartbeat(db, old, now=recovered_time)
            await db.commit()
        async with sessions() as db:
            recovered = await jobs.claim(db, now=recovered_time)
            await db.commit()
        assert recovered.job_id == old.job_id and recovered.token != old.token
        async with sessions() as db:
            assert not await jobs.heartbeat(db, old, now=recovered_time)
            assert await jobs.finish_index(db, old, "a" * 32, 10, 20) == "lease_lost"
            await db.commit()
        async with sessions() as db:
            stored = await db.get(KnowledgeJob, old.job_id)
            assert stored.status == "running" and stored.attempts == 2 and stored.lease_token == recovered.token
            assert not (await db.scalars(select(AdminAudit).where(
                AdminAudit.action == "knowledge.index.activated"))).all()
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")


def test_expired_last_attempt_and_expired_cancel_are_terminal(monkeypatch, tmp_path):
    """
    失联执行器的最后一次尝试不能无限重试；取消中的失联任务最终进入取消终态。
    """
    async def scenario(client, sessions, _redis):
        """
        两种恢复均释放去重槽，新请求可创建新任务，旧任务不会复活。
        """
        headers = await login(client)
        for cancel, expected in ((False, "failed"), (True, "cancelled")):
            queued = await request_index(client, headers)
            async with sessions() as db:
                lease = await jobs.claim(db)
                await db.commit()
            async with sessions() as db:
                row = await db.get(KnowledgeJob, queued["id"])
                row.attempts, row.lease_until, row.cancel_requested = row.max_attempts, 0, cancel
                await db.commit()
            async with sessions() as db:
                assert await jobs.claim(db) is None
                await db.commit()
            async with sessions() as db:
                row = await db.get(KnowledgeJob, queued["id"])
                assert row.status == expected and not row.lease_token
                assert not (await db.scalars(select(KnowledgeJobSlot))).all()
                assert await jobs.finish_index(db, lease, "b" * 32, 0, 0) == "lease_lost"
                await db.commit()
    run_scenario(monkeypatch, scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'expired.db'}")


@pytest.mark.parametrize("failure, expected", [
    (OSError, "storage_failed"), (indexes.FtsUnavailable, "fts_unavailable"), (RuntimeError, "index_failed"),
])
def test_failures_backoff_retry_limit_and_no_exception_payload(monkeypatch, tmp_path, failure, expected):
    """
    构建故障只落白名单代码，按退避和重试上限终止，不把原文或路径写入审计。
    """
    async def broken(*args):
        """
        模拟含私有载荷的异常，断言服务只保留固定摘要错误码。
        """
        raise failure("synthetic-private-body-and-cookie-must-not-be-recorded")

    async def scenario(client, sessions, _redis):
        """
        连续三次实际 worker 尝试失败，退避期间不能被提前认领。
        """
        headers = await login(client)
        queued = await request_index(client, headers)
        monkeypatch.setattr(indexes, "build_snapshot", broken)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        worker = KnowledgeWorker(sessions, directory)
        for attempt in range(1, 4):
            result = await worker.run_once()
            assert result["outcome"] == ("queued" if attempt < 3 else "failed")
            assert await worker.run_once() is None
            async with sessions() as db:
                row = await db.get(KnowledgeJob, queued["id"])
                assert row.attempts == attempt and row.outcome == expected
                assert not row.lease_token
                if attempt < 3:
                    assert row.available_at > time.time()
                    row.available_at = 0
                    await db.commit()
        listed = await client.get(ROOT + "/jobs")
        assert "synthetic-private" not in listed.text
        async with sessions() as db:
            audits = (await db.scalars(select(AdminAudit))).all()
            assert all("synthetic-private" not in row.detail for row in audits)
            assert not (await db.scalars(select(KnowledgeJobSlot))).all()
        assert not list(directory.iterdir())
    run_scenario(monkeypatch, scenario)


def test_timeout_cancels_build_and_retries_safely(monkeypatch, tmp_path):
    """
    有界构建超时会取消实际协程并进入受控重试，不能留下继续写文件的后台任务。
    """
    completed = []

    async def slow(*args):
        """
        观察取消是否到达执行中的协程，而不是只在外层标记超时。
        """
        try:
            await asyncio.Event().wait()
        finally:
            completed.append("cancelled")

    async def scenario(client, sessions, _redis):
        """
        短测试期限代替十分钟生产期限，不等待真实长时间。
        """
        headers = await login(client)
        queued = await request_index(client, headers)
        monkeypatch.setattr(indexes, "build_snapshot", slow)
        monkeypatch.setattr(worker_module, "BUILD_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        assert (await KnowledgeWorker(sessions, directory).run_once())["outcome"] == "queued"
        assert completed == ["cancelled"]
        async with sessions() as db:
            row = await db.get(KnowledgeJob, queued["id"])
            assert row.outcome == "timeout" and row.status == "queued"
    run_scenario(monkeypatch, scenario)


def test_new_request_while_cancel_pending_gets_own_slot(monkeypatch, tmp_path):
    """
    已取消的运行任务不能吞掉后来触发的新任务，也不能清除新任务的去重槽。
    """
    async def scenario(client, sessions, _redis):
        """
        在旧执行器完成取消前创建新任务，验证两个任务的槽位所有权独立。
        """
        headers = await login(client)
        first = await request_index(client, headers)
        async with sessions() as db:
            old = await jobs.claim(db)
            await db.commit()
        cancelled = await client.post(ROOT + "/jobs/" + first["id"] + "/cancel", headers=headers,
                                      json={"expected_version": 2})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "running"
        second = await request_index(client, headers)
        assert second["id"] != first["id"]
        async with sessions() as db:
            assert await jobs.finish_index(db, old, "c" * 32, 0, 0) == "cancelled"
            await db.commit()
        async with sessions() as db:
            slot = await db.get(KnowledgeJobSlot, "index")
            assert slot.job_id == second["id"]
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        assert (await KnowledgeWorker(sessions, directory).run_once())["job_id"] == second["id"]
    run_scenario(monkeypatch, scenario)


def test_heartbeat_monitor_interrupts_cancelled_build(monkeypatch, tmp_path):
    """
    真正的续租循环发现取消后中断执行协程，不必等十分钟构建超时。
    """
    async def scenario(client, sessions, _redis):
        """
        执行器运行期间通过真实 API 取消，观察构建 finally 与持久化终态。
        """
        started = asyncio.Event()
        stopped = []

        async def slow(*args):
            """
            挂起真实构建协程，使续租监视器有机会读取数据库取消请求。
            """
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(True)

        headers = await login(client)
        queued = await request_index(client, headers)
        monkeypatch.setattr(indexes, "build_snapshot", slow)
        monkeypatch.setattr(jobs, "LEASE_SECONDS", 3)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        directory = indexes.private_directory(str(tmp_path / "private"))
        running = asyncio.create_task(KnowledgeWorker(sessions, directory).run_once())
        await asyncio.wait_for(started.wait(), timeout=3)
        cancelled = await client.post(ROOT + "/jobs/" + queued["id"] + "/cancel", headers=headers,
                                      json={"expected_version": 2})
        assert cancelled.status_code == 200
        assert (await asyncio.wait_for(running, timeout=5))["outcome"] == "cancelled"
        assert stopped == [True]
        async with sessions() as db:
            row = await db.get(KnowledgeJob, queued["id"])
            assert row.status == "cancelled" and not row.lease_token
    run_scenario(monkeypatch, scenario)


def test_job_tables_compile_for_postgresql():
    """
    PostgreSQL 方言检查租约、去重外键与代次约束，不代替真实数据库集成。
    """
    statements = []

    def capture(statement, *args, **kwargs):
        """
        记录实际编译结果，不连接生产数据库。
        """
        statements.append(str(statement.compile(dialect=engine.dialect)))

    engine = create_mock_engine("postgresql://", capture)
    tables = [Base.metadata.tables[name] for name in ("KNOWLEDGE_STATE", "KNOWLEDGE_JOB", "KNOWLEDGE_JOB_SLOT")]
    Base.metadata.create_all(engine, tables=tables, checkfirst=False)
    ddl = "\n".join(statements)
    assert "ck_knowledge_state_generation" in ddl and "ck_knowledge_job_attempts" in ddl
    assert 'REFERENCES "KNOWLEDGE_JOB" (id)' in ddl and "UNIQUE (job_id)" in ddl
