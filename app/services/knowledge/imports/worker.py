"""
导入任务执行：有界预检、计划版本复核和原子待审修订写入。
"""

import asyncio
import contextlib
import json
import time

from sqlalchemy import bindparam, insert, select, update

from app.models.knowledge import (
    KnowledgeCollection, KnowledgeDocument, KnowledgeImport, KnowledgeImportReceipt, KnowledgeImportTask,
    KnowledgeRevision, KnowledgeSource, timestamp,
)
from app.schemas.knowledge import ImportApproval, ImportPreflight
from app.services.knowledge import jobs
from app.services.knowledge.imports import files, plans
from app.services.knowledge.imports.catalog import validate_approval

APPLY_TIMEOUT_SECONDS = 30


async def load_task(db, lease):
    """
    只读取当前任务绑定的批次，不接受执行器外部传入来源或路径。
    """
    task = await db.get(KnowledgeImportTask, lease.job_id)
    batch = await db.get(KnowledgeImport, task.import_id) if task else None
    if batch is None or batch.current_job_id != lease.job_id:
        raise plans.PlanBlocked("plan_stale")
    return task, batch


async def blocked(sessions, lease, code: str) -> dict:
    """
    数据或版本问题要求重新审核，不对同一无效计划自动重复写入。
    """
    async with sessions() as db:
        await jobs.lock_state(db)
        _task, batch = await load_task(db, lease)
        batch.error_code = code
        outcome = await jobs.finish_import(db, lease, batch, status="blocked", outcome=code, failed=True)
        if outcome == "lease_lost":
            await db.rollback()
        else:
            await db.commit()
    return {"job_id": lease.job_id, "outcome": outcome}


async def preflight(sessions, directory, lease, batch, source, collection) -> dict:
    """
    构建完整计划后再在租约事务中接纳；不把部分预检结果当成可导入。
    """
    mapping = ImportPreflight(expected_version=batch.version, **json.loads(batch.mapping))
    plan_id, plan_hash, summary = "", "", {}
    error_code = ""
    try:
        plan_id, plan_hash, summary = await plans.build_plan(sessions, directory, batch, mapping, source, collection)
    except plans.PlanBlocked as error:
        error_code = error.code
        summary = {"rows": 0, "actions": {}, "flags": {error_code: 1}}
    attempted = False
    try:
        async with sessions() as db:
            await jobs.lock_state(db)
            _task, current = await load_task(db, lease)
            owned = await jobs.owned_job(db, lease)
            latest = await db.get(KnowledgeSource, source.id)
            if owned is None:
                await db.rollback()
                return {"job_id": lease.job_id, "outcome": "lease_lost"}
            if latest.version != current.source_version:
                error_code = "source_changed"
            if not owned.cancel_requested:
                current.plan_id, current.plan_hash = (plan_id, plan_hash) if not error_code else ("", "")
                current.summary, current.error_code = plans.encoded(summary), error_code
            outcome = await jobs.finish_import(
                db, lease, current, status="blocked" if error_code else "ready",
                outcome="preflight_blocked" if error_code else "preflight_ready",
            )
            if outcome == "lease_lost":
                await db.rollback()
            else:
                attempted = True
                await db.commit()
            if outcome != "preflight_ready" and plan_id:
                files.path(directory, batch.id, plan_id).unlink(missing_ok=True)
            return {"job_id": lease.job_id, "outcome": outcome}
    finally:
        if plan_id and not attempted:
            files.path(directory, batch.id, plan_id).unlink(missing_ok=True)


async def apply(sessions, directory, lease, reviewed) -> dict:
    """
    对所选行在一个有界事务中比较版本、写修订和回执，发布指针保持不变。
    """
    if files.digest(files.path(directory, reviewed.id, reviewed.plan_id)) != reviewed.plan_hash:
        raise plans.PlanBlocked("plan_changed")
    approval = ImportApproval(expected_version=reviewed.version, **json.loads(reviewed.confirmation))
    validate_approval(directory, reviewed, approval)
    with contextlib.closing(plans.open_plan(directory, reviewed)) as plan:
        async with asyncio.timeout(APPLY_TIMEOUT_SECONDS), sessions() as db:
            await jobs.lock_state(db)
            _task, batch = await load_task(db, lease)
            owned = await jobs.owned_job(db, lease)
            if owned is None:
                return {"job_id": lease.job_id, "outcome": "lease_lost"}
            if owned.cancel_requested:
                outcome = await jobs.finish_import(db, lease, batch, status="cancelled", outcome="cancelled")
                await db.commit()
                return {"job_id": lease.job_id, "outcome": outcome}
            if batch.plan_hash != reviewed.plan_hash or batch.status != "apply_queued":
                raise plans.PlanBlocked("plan_stale")
            skip = set(approval.skip_lines)
            cursor = plan.execute("SELECT * FROM rows ORDER BY line")
            expected = {}
            # 先分批锁定并核对全部目标，再锁来源，避免和手工追加的顺序相反。
            while candidates := cursor.fetchmany(200):
                selected = [row for row in candidates if row["line"] not in skip and row["action"] != "excluded"]
                if not selected:
                    continue
                current = (await db.execute(select(KnowledgeDocument.id, KnowledgeDocument.external_id,
                                                  KnowledgeDocument.version, KnowledgeDocument.head_revision).where(
                    KnowledgeDocument.source_id == batch.source_id,
                    KnowledgeDocument.external_id.in_([row["external_id"] for row in selected]),
                ).order_by(KnowledgeDocument.id).with_for_update())).all()
                by_external = {row.external_id: row for row in current}
                for row in selected:
                    old = by_external.get(row["external_id"])
                    if row["base_version"] is None:
                        if old is not None:
                            raise plans.PlanBlocked("plan_stale")
                    elif old is None or old.id != row["document_id"] or old.version != row["base_version"]:
                        raise plans.PlanBlocked("plan_stale")
                    expected[row["line"]] = old.version if old else None
            source = await db.scalar(select(KnowledgeSource).where(
                KnowledgeSource.id == batch.source_id,
            ).with_for_update())
            if source.connector != "jsonl" or source.version != batch.source_version:
                raise plans.PlanBlocked("source_changed")
            result = {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "excluded": 0}
            cursor = plan.execute("SELECT * FROM rows ORDER BY line")
            while candidates := cursor.fetchmany(200):
                new_documents, updates, revisions, receipts = [], [], [], []
                for row in candidates:
                    excluded = row["action"] == "excluded"
                    skipped = excluded or row["line"] in skip
                    action = "skipped" if skipped else ("updated" if row["action"] == "conflict" else row["action"])
                    result["excluded" if excluded else action] += 1
                    number = None
                    if not skipped:
                        base_version = expected[row["line"]]
                        number = row["base_revision"] if action == "unchanged" else (row["base_revision"] or 0) + 1
                        if action == "new":
                            new_documents.append({"id": row["document_id"], "source_id": source.id,
                                                  "external_id": row["external_id"], "head_revision": 1})
                        elif action == "updated":
                            updates.append({"target": row["document_id"], "prior": base_version, "head": number})
                        if action != "unchanged":
                            revisions.append({**json.loads(row["payload"]), "document_id": row["document_id"],
                                              "number": number, "state": "review",
                                              "created_by": "knowledge-import:" + batch.id})
                    receipts.append({
                        "import_id": batch.id, "line": row["line"], "outcome": action, "flags": row["flags"],
                        "document_id": None if skipped else row["document_id"], "revision_number": number,
                        "base_revision": None if skipped else row["base_revision"],
                    })
                if new_documents:
                    await db.execute(insert(KnowledgeDocument), new_documents)
                if updates:
                    changed = await db.execute(update(KnowledgeDocument.__table__).where(
                        KnowledgeDocument.id == bindparam("target"), KnowledgeDocument.version == bindparam("prior"),
                    ).values(head_revision=bindparam("head"), version=bindparam("prior") + 1, updated_at=timestamp()), updates)
                    if changed.rowcount >= 0 and changed.rowcount != len(updates):
                        raise plans.PlanBlocked("plan_stale")
                    if changed.rowcount < 0:
                        actual = dict((await db.execute(select(KnowledgeDocument.id, KnowledgeDocument.version).where(
                            KnowledgeDocument.id.in_([item["target"] for item in updates]),
                        ))).all())
                        if any(actual.get(item["target"]) != item["prior"] + 1 for item in updates):
                            raise plans.PlanBlocked("plan_stale")
                if revisions:
                    await db.execute(insert(KnowledgeRevision), revisions)
                await db.execute(insert(KnowledgeImportReceipt), receipts)
                await asyncio.sleep(0)
            batch.result, batch.error_code = plans.encoded(result), ""
            outcome = await jobs.finish_import(
                db, lease, batch, status="imported",
                outcome="imported_with_skips" if result["skipped"] or result["excluded"] else "imported",
                documents=result["new"] + result["updated"],
            )
            if outcome == "lease_lost":
                await db.rollback()
            else:
                await db.commit()
            return {"job_id": lease.job_id, "outcome": outcome}


async def execute(sessions, directory, lease) -> dict:
    """
    从持久化任务分派固定阶段；只有完整且版本仍匹配的计划可以导入。
    """
    try:
        async with sessions() as db:
            await jobs.lock_state(db)
            owned = await jobs.owned_job(db, lease)
            if owned is None:
                await db.rollback()
                return {"job_id": lease.job_id, "outcome": "lease_lost"}
            task, batch = await load_task(db, lease)
            if owned.cancel_requested:
                outcome = await jobs.finish_import(db, lease, batch, status="cancelled", outcome="cancelled")
                await db.commit()
                return {"job_id": lease.job_id, "outcome": outcome}
            if batch.status != ("preflight_queued" if task.phase == "preflight" else "apply_queued"):
                raise plans.PlanBlocked("plan_stale")
            source = await db.get(KnowledgeSource, batch.source_id)
            collection = await db.get(KnowledgeCollection, source.collection_id)
            if batch.expires_at <= time.time():
                raise plans.PlanBlocked("expired")
            if task.phase == "preflight":
                # 删除旧计划也属于写副作用；必须在同一租约锁内，旧执行器不能删新计划。
                files.remove_completed_plans(directory, batch.id)
                files.check_quota(directory)
            await db.commit()
        if task.phase == "preflight":
            return await preflight(sessions, directory, lease, batch, source, collection)
        return await apply(sessions, directory, lease, batch)
    except plans.PlanBlocked as error:
        return await blocked(sessions, lease, error.code)
