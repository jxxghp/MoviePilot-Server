"""
持久化知识任务的去重、租约、取消与索引切换；调用者负责事务提交。
"""

import time
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.models.knowledge import (
    KnowledgeImport, KnowledgeImportTask, KnowledgeJob, KnowledgeJobSlot, KnowledgeState, new_identity, timestamp,
)

JOB_FIELDS = ("id", "version", "kind", "source_id", "status", "attempts", "max_attempts", "available_at",
              "cancel_requested", "processed_documents", "processed_chunks", "outcome", "created_at",
              "updated_at", "started_at", "finished_at", "created_by")
TERMINAL = {"succeeded", "failed", "cancelled"}
LEASE_SECONDS = 60
ERROR_CODES = {"index_failed", "import_failed", "collect_failed", "storage_failed", "database_failed", "fts_unavailable", "timeout"}


@dataclass(frozen=True)
class JobLease:
    """
    独立执行器持有的单次所有权，不通过 API、审计或日志暴露 token。
    """

    job_id: str
    token: str
    generation: int
    kind: str = "index"


async def lock_state(db: AsyncSession) -> KnowledgeState:
    """
    先锁全局知识事实，再锁文档或任务，保证队列去重与激活的统一顺序。
    """
    dialect = db.get_bind().dialect.name
    insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
    await db.execute(insert(KnowledgeState).values(id="main", generation=0, active_generation=0, active_snapshot="")
                     .on_conflict_do_nothing(index_elements=["id"]))
    return await db.scalar(select(KnowledgeState).where(KnowledgeState.id == "main")
                           .with_for_update().execution_options(populate_existing=True))


def job_data(row: KnowledgeJob) -> dict:
    """
    展示执行状态和计数，不返回租约秘密、原文异常或本地存储路径。
    """
    return {key: getattr(row, key) for key in JOB_FIELDS}


async def enqueue_index(db: AsyncSession, actor: str, *, dirty: bool = True) -> KnowledgeJob:
    """
    在发布事务内递增事实代次并合并活动索引任务，不把排队当作完成。
    """
    state = await lock_state(db)
    if dirty:
        state.generation += 1
    slot = await db.get(KnowledgeJobSlot, "index", populate_existing=True)
    if slot:
        existing = await db.get(KnowledgeJob, slot.job_id, populate_existing=True)
        if existing and existing.status not in TERMINAL and not existing.cancel_requested:
            await db.flush()
            return existing
        await db.delete(slot)
        await db.flush()
    row = KnowledgeJob(kind="index", available_at=time.time(), created_by=actor)
    db.add(row)
    await db.flush()
    db.add(KnowledgeJobSlot(key="index", job_id=row.id))
    await db.flush()
    return row


async def release_slot(db: AsyncSession, job_id: str) -> None:
    """
    只释放仍属于此任务的槽位，旧执行器不能清除后来排入的新任务。
    """
    await db.execute(delete(KnowledgeJobSlot).where(KnowledgeJobSlot.job_id == job_id))


async def list_jobs(db: AsyncSession, page: int, size: int) -> dict:
    """
    分页列出真实任务状态，不携带内部租约 token。
    """
    total = await db.scalar(select(func.count()).select_from(KnowledgeJob))
    rows = (await db.scalars(select(KnowledgeJob).order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id)
                             .offset((page - 1) * size).limit(size))).all()
    return {"items": [job_data(row) for row in rows], "total": total or 0, "page": page, "size": size}


async def cancel_job(db: AsyncSession, job_id: str, expected_version: int, actor: str) -> dict:
    """
    等待中的任务立即取消；运行中的任务先标记，激活与心跳均会拒绝继续。
    """
    await lock_state(db)
    row = await db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
    if row is None:
        raise HTTPException(404, "知识任务不存在")
    if row.version != expected_version:
        raise HTTPException(409, "知识任务状态已改变，请刷新后重试")
    if row.status not in TERMINAL and not row.cancel_requested:
        row.cancel_requested = True
        row.version += 1
        row.updated_at = timestamp()
        if row.status == "queued":
            row.status, row.outcome, row.finished_at = "cancelled", "cancelled", timestamp()
            await release_slot(db, job_id)
            await settle_import(db, row)
        record(db, actor, "knowledge.job.cancel", job_id, {"status": row.status})
    await db.flush()
    return job_data(row)


async def claim(db: AsyncSession, *, now: float | None = None) -> JobLease | None:
    """
    数据库串行认领受支持任务，失联租约受重试上限和随机所有权约束。
    """
    clock = time.time() if now is None else now
    state = await lock_state(db)
    active = set((await db.scalars(select(KnowledgeJob.kind).where(
        KnowledgeJob.kind.in_(["import", "collect"]), KnowledgeJob.status == "running",
        KnowledgeJob.lease_until > clock,
    ))).all())
    candidates = (await db.scalars(select(KnowledgeJob).where(
        KnowledgeJob.kind.in_(set(("index", "import", "collect")) - active),
        ((KnowledgeJob.status == "queued") & (KnowledgeJob.available_at <= clock)) |
        ((KnowledgeJob.status == "running") & (KnowledgeJob.lease_until <= clock)),
    ).order_by(KnowledgeJob.available_at, KnowledgeJob.id).limit(20).with_for_update())).all()
    for row in candidates:
        if row.cancel_requested or row.attempts >= row.max_attempts:
            row.status = "cancelled" if row.cancel_requested else "failed"
            row.outcome = "cancelled" if row.cancel_requested else "lease_expired"
            row.finished_at, row.updated_at = timestamp(), timestamp()
            row.lease_token, row.lease_until, row.version = "", None, row.version + 1
            await release_slot(db, row.id)
            await settle_import(db, row)
            continue
        row.status, row.lease_token, row.lease_until = "running", new_identity(), clock + LEASE_SECONDS
        row.attempts, row.version = row.attempts + 1, row.version + 1
        row.started_at, row.updated_at, row.outcome = timestamp(), timestamp(), ""
        await db.flush()
        return JobLease(row.id, row.lease_token, state.generation, row.kind)
    await db.flush()
    return None


async def heartbeat(db: AsyncSession, lease: JobLease, *, now: float | None = None) -> bool:
    """
    只续约未过期且未被取消的当前所有权，不复活已经失联或被替代的执行器。
    """
    clock = time.time() if now is None else now
    result = await db.execute(update(KnowledgeJob).where(
        KnowledgeJob.id == lease.job_id, KnowledgeJob.status == "running",
        KnowledgeJob.lease_token == lease.token, KnowledgeJob.lease_until > clock,
        KnowledgeJob.cancel_requested.is_(False),
    ).values(lease_until=clock + LEASE_SECONDS).execution_options(synchronize_session=False))
    return result.rowcount == 1


async def owned_job(db: AsyncSession, lease: JobLease) -> KnowledgeJob | None:
    """
    完成前复核租约和当前所有权；失效执行器不能写终态、审计或索引指针。
    """
    return await db.scalar(select(KnowledgeJob).where(
        KnowledgeJob.id == lease.job_id, KnowledgeJob.status == "running",
        KnowledgeJob.lease_token == lease.token, KnowledgeJob.lease_until > time.time(),
    ).with_for_update().execution_options(populate_existing=True))


async def finish_index(db: AsyncSession, lease: JobLease, snapshot: str, documents: int, chunks: int) -> str:
    """
    仅激活当前代次的完整快照；事实已变化时保留旧在线快照并补排新任务。
    """
    state = await lock_state(db)
    row = await owned_job(db, lease)
    if row is None:
        return "lease_lost"
    if row.cancel_requested:
        result = "cancelled"
        row.status = "cancelled"
    elif state.generation != lease.generation:
        result = "superseded"
        row.status = "succeeded"
    else:
        result = "activated"
        state.active_snapshot, state.active_generation = snapshot, lease.generation
        row.status = "succeeded"
    row.outcome, row.processed_documents, row.processed_chunks = result, documents, chunks
    row.finished_at, row.updated_at, row.version = timestamp(), timestamp(), row.version + 1
    row.lease_token, row.lease_until = "", None
    await release_slot(db, row.id)
    if result == "superseded":
        await enqueue_index(db, row.created_by, dirty=False)
    record(db, "knowledge-worker", "knowledge.index." + result, row.id,
           {"generation": lease.generation, "documents": documents, "chunks": chunks})
    await db.flush()
    return result


async def fail_job(db: AsyncSession, lease: JobLease, error_code: str) -> str:
    """
    原文异常不能落库；失败按上限退避重试，取消或失效执行器不再重排。
    """
    await lock_state(db)
    row = await owned_job(db, lease)
    if row is None:
        return "lease_lost"
    code = error_code if error_code in ERROR_CODES else "index_failed"
    if row.cancel_requested:
        row.status, row.outcome = "cancelled", "cancelled"
    else:
        row.status, row.outcome = ("queued" if row.attempts < row.max_attempts else "failed"), code
    if row.status in TERMINAL:
        row.finished_at = timestamp()
        await release_slot(db, row.id)
        await settle_import(db, row)
    else:
        row.available_at = time.time() + min(15 * 2 ** (row.attempts - 1), 120)
    row.lease_token, row.lease_until = "", None
    row.updated_at, row.version = timestamp(), row.version + 1
    record(db, "knowledge-worker", "knowledge.job." + row.status, row.id, {"code": row.outcome})
    await db.flush()
    return row.status


async def settle_import(db: AsyncSession, job: KnowledgeJob) -> None:
    """
    取消或终态失败同步对应批次，但旧任务不能改变后来任务绑定的批次。
    """
    if job.kind != "import":
        return
    task = await db.get(KnowledgeImportTask, job.id)
    batch = await db.get(KnowledgeImport, task.import_id) if task else None
    if batch and batch.current_job_id == job.id and job.status in {"cancelled", "failed"}:
        batch.status, batch.error_code = job.status, job.outcome
        batch.version, batch.updated_at = batch.version + 1, timestamp()


async def finish_import(db: AsyncSession, lease: JobLease, batch: KnowledgeImport, *, status: str,
                        outcome: str, documents: int = 0, failed: bool = False) -> str:
    """
    在导入事务内完成批次、回执与任务，拒绝失效或被取消的所有权。
    """
    await lock_state(db)
    row = await owned_job(db, lease)
    if row is None or batch.current_job_id != lease.job_id:
        return "lease_lost"
    if row.cancel_requested:
        status, outcome = "cancelled", "cancelled"
    batch.status, batch.version, batch.updated_at = status, batch.version + 1, timestamp()
    row.status = "cancelled" if status == "cancelled" else ("failed" if failed else "succeeded")
    row.outcome, row.processed_documents = outcome, documents
    row.finished_at, row.updated_at, row.version = timestamp(), timestamp(), row.version + 1
    row.lease_token, row.lease_until = "", None
    await release_slot(db, row.id)
    record(db, "knowledge-worker", "knowledge.import." + outcome, batch.id, {"documents": documents})
    await db.flush()
    return outcome
