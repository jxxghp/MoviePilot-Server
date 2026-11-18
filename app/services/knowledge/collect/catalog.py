"""
明确采集策略、同来源任务去重与独立 worker 周期调度；请求不直接出网。
"""

import json
import time

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.admin.audit import record
from app.models.knowledge import (
    KnowledgeCollectCandidate, KnowledgeCollectReceipt, KnowledgeCollectState, KnowledgeCollectTask, KnowledgeCollection,
    KnowledgeCredential, KnowledgeDocument, KnowledgeJob, KnowledgeJobSlot, KnowledgeRevision, KnowledgeSite, KnowledgeSource,
)
from app.schemas.knowledge import RevisionPayload
from app.services.knowledge import jobs
from app.services.knowledge.catalog import commit, get_row, replace_values
from app.services.knowledge import documents

CONNECTORS = ("github", "http", "nexusphp")


async def active_source(db, source_id):
    """手动触发和提交都检查当前集合 / 站点启停，不只检查来源开关。"""
    source = await get_row(db, KnowledgeSource, source_id, for_update=True)
    parent = await get_row(db, KnowledgeCollection, source.collection_id)
    site = await db.get(KnowledgeSite, source.site_id) if source.site_id else None
    if (source.connector not in CONNECTORS or not source.enabled or not parent.enabled
            or (source.site_id and (site is None or not site.enabled))):
        raise HTTPException(409, "knowledge_source_unavailable")
    return source, parent


async def state_for(db, source, *, now=None):
    """只保存有界状态；首次观察不立即排队，尊重明确刷新间隔。"""
    state = await db.get(KnowledgeCollectState, source.id)
    if state is None:
        clock = time.time() if now is None else now
        state = KnowledgeCollectState(source_id=source.id, source_version=source.version,
                                      next_at=clock + source.refresh_minutes * 60 if source.refresh_minutes else None)
        db.add(state)
        await db.flush()
    return state


async def read(db, source_id):
    """后台查看策略、下一周期和真实最近任务，不回显秘密或正文。"""
    source = await get_row(db, KnowledgeSource, source_id)
    state = await db.get(KnowledgeCollectState, source_id)
    job = await db.get(KnowledgeJob, state.last_job_id) if state and state.last_job_id else None
    task = await db.get(KnowledgeCollectTask, job.id) if job else None
    return {"source_version": source.version, "policy": json.loads(state.policy) if state else {"version_scope": "unknown"},
            "next_at": state.next_at if state else None, "job": jobs.job_data(job) if job else None,
            "result": json.loads(task.result) if task else {}}


async def save_policy(db, source_id, payload, actor):
    """明确版本策略变动也递增来源版本，旧任务不能套用新范围提交内容。"""
    await jobs.lock_state(db)
    source = await get_row(db, KnowledgeSource, source_id, for_update=True)
    if source.connector not in CONNECTORS:
        raise HTTPException(422, "knowledge_connector_unsupported")
    parent = await get_row(db, KnowledgeCollection, source.collection_id)
    if payload.version_scope == "range" and not parent.product:
        raise HTTPException(422, "knowledge_product_required")
    state = await state_for(db, source)
    await replace_values(db, source, payload.expected_version, {})
    state.policy = json.dumps(payload.model_dump(exclude={"expected_version"}), sort_keys=True)
    record(db, actor, "knowledge.collect.policy", source_id)
    await commit(db)
    await db.refresh(source)
    return await read(db, source_id)


async def enqueue(db, source_id, expected_version, actor, *, now=None):
    """同配置合并；配置已改变时取消旧任务并保留新任务独立槽，避免复用过时请求。"""
    await jobs.lock_state(db)
    source, _parent = await active_source(db, source_id)
    if source.version != expected_version:
        raise HTTPException(409, "knowledge_source_changed")
    secret = await db.scalar(select(KnowledgeCredential.version).where(KnowledgeCredential.source_id == source_id))
    credential_version = secret or 0
    slot = await db.get(KnowledgeJobSlot, "source:" + source_id)
    if slot:
        previous = await db.get(KnowledgeJob, slot.job_id)
        task = await db.get(KnowledgeCollectTask, slot.job_id)
        if previous and previous.status not in jobs.TERMINAL and not previous.cancel_requested:
            if task and task.source_version == source.version and task.credential_version == credential_version:
                return previous
            await jobs.cancel_job(db, previous.id, previous.version, actor)
        await jobs.release_slot(db, slot.job_id)
        await db.flush()
    clock = time.time() if now is None else now
    state = await state_for(db, source, now=clock)
    job = KnowledgeJob(kind="collect", source_id=source.id, available_at=clock, created_by=actor)
    db.add(job)
    await db.flush()
    db.add(KnowledgeCollectTask(job_id=job.id, source_version=source.version,
                                credential_version=credential_version, policy=state.policy))
    db.add(KnowledgeJobSlot(key="source:" + source.id, job_id=job.id))
    state.last_job_id, state.source_version = job.id, source.version
    state.next_at = clock + source.refresh_minutes * 60 if source.refresh_minutes else None
    record(db, actor, "knowledge.collect.request", source.id, {"job": job.id})
    await db.flush()
    return job


async def schedule(db, *, now=None):
    """各进程由同一 SQL 锁观察到期来源，不在保存时或每个 Web worker 中启动爬虫。"""
    clock = time.time() if now is None else now
    await jobs.lock_state(db)
    sources = (await db.scalars(select(KnowledgeSource).join(
        KnowledgeCollection, KnowledgeCollection.id == KnowledgeSource.collection_id,
    ).outerjoin(KnowledgeSite, KnowledgeSite.id == KnowledgeSource.site_id).outerjoin(
        KnowledgeCollectState, KnowledgeCollectState.source_id == KnowledgeSource.id,
    ).where(
        KnowledgeSource.connector.in_(CONNECTORS), KnowledgeSource.enabled.is_(True),
        KnowledgeCollection.enabled.is_(True), KnowledgeSource.refresh_minutes > 0,
        or_(KnowledgeSource.site_id.is_(None), KnowledgeSite.enabled.is_(True)),
        or_(KnowledgeCollectState.source_id.is_(None), KnowledgeCollectState.source_version != KnowledgeSource.version,
            KnowledgeCollectState.next_at <= clock),
    ).order_by(KnowledgeSource.id).limit(20))).all()
    queued = 0
    for source in sources:
        state = await db.get(KnowledgeCollectState, source.id)
        if state is None:
            await state_for(db, source, now=clock)
        elif state.source_version != source.version:
            state.source_version, state.next_at = source.version, clock + source.refresh_minutes * 60
        elif state.next_at is not None and state.next_at <= clock:
            await enqueue(db, source.id, source.version, "knowledge-scheduler", now=clock)
            queued += 1
    await db.commit()
    return queued


async def receipts(db, job_id, page, size):
    """分页查看真实逐页结果；失败响应不入库，成功正文通过现有修订详情查看。"""
    job = await get_row(db, KnowledgeJob, job_id)
    task = await db.get(KnowledgeCollectTask, job_id)
    if job.kind != "collect" or task is None:
        raise HTTPException(404, "knowledge_collect_task_missing")
    total = await db.scalar(select(func.count()).select_from(KnowledgeCollectReceipt).where(
        KnowledgeCollectReceipt.job_id == job_id))
    rows = (await db.scalars(select(KnowledgeCollectReceipt).where(
        KnowledgeCollectReceipt.job_id == job_id,
    ).order_by(KnowledgeCollectReceipt.external_id).offset((page - 1) * size).limit(size))).all()
    fields = ("external_id", "document_id", "revision_number", "base_revision", "outcome", "source_commit", "candidate_id")
    return {"job": jobs.job_data(job), "result": json.loads(task.result), "total": total or 0, "page": page, "size": size,
            "items": [{**{key: getattr(row, key) for key in fields}, "flags": json.loads(row.flags)} for row in rows]}


def candidate_data(row):
    """候选状态摘要不含正文，正文仅在明确详情请求中返回。"""
    fields = ("id", "version", "document_id", "document_version", "source_version", "base_revision",
              "applied_revision", "discarded")
    return {**{key: getattr(row, key) for key in fields}, "flags": json.loads(row.flags)}


async def read_candidate(db, identity):
    """管理员对照当前人工正文和已采集候选，历史回执无需保存重复正文。"""
    row = await get_row(db, KnowledgeCollectCandidate, identity)
    return {"candidate": candidate_data(row), "proposal": json.loads(row.payload),
            "current": await documents.read_document(db, row.document_id, None)}


async def append_candidate(db, identity, payload, actor):
    """显式接受上游候选仅追加待审修订，旧人工版本和发布指针都保留。"""
    await jobs.lock_state(db)
    candidate = await get_row(db, KnowledgeCollectCandidate, identity, for_update=True)
    document = await get_row(db, KnowledgeDocument, candidate.document_id, for_update=True)
    source, _parent = await active_source(db, document.source_id)
    if (candidate.version != payload.expected_version or document.version != payload.expected_document_version
            or candidate.document_version != document.version or source.version != candidate.source_version
            or candidate.applied_revision is not None or candidate.discarded):
        raise HTTPException(409, "knowledge_collect_candidate_changed")
    if json.loads(candidate.flags) and not payload.acknowledge_warnings:
        raise HTTPException(422, "knowledge_collect_warnings_required")
    proposal = RevisionPayload(**json.loads(candidate.payload))
    parent = await documents.collection_for_source(db, source, proposal)
    number = document.head_revision + 1
    await replace_values(db, document, payload.expected_document_version, {"head_revision": number})
    db.add(KnowledgeRevision(document_id=document.id, number=number, state="review",
                             created_by="knowledge-review:" + candidate.id,
                             **documents.revision_values(proposal, parent.product)))
    await replace_values(db, candidate, payload.expected_version, {"applied_revision": number})
    record(db, actor, "knowledge.collect.accept", candidate.id, {"revision": number})
    await commit(db)
    await db.refresh(candidate)
    return candidate_data(candidate)


async def discard_candidate(db, identity, expected_version, actor):
    """忽略已审阅差异而不改变文档；以后上游或人工版本改变仍会生成新候选。"""
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeCollectCandidate, identity, for_update=True)
    if row.applied_revision is not None:
        raise HTTPException(409, "knowledge_collect_candidate_applied")
    await replace_values(db, row, expected_version, {"discarded": True})
    record(db, actor, "knowledge.collect.discard", identity)
    await commit(db)
    await db.refresh(row)
    return candidate_data(row)
