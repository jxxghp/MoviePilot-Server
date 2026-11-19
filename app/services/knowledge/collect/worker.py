"""
采集的网络阶段与有界事务分离，来源 / 租约 / 人工修订保护均由当前 SQL 再确认。
"""

import asyncio
import hashlib
import json
import time
from collections import Counter

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, exists, func, select

from app.admin.audit import record
from app.admin.config import knowledge_enabled
from app.core.config import settings
from app.models.knowledge import (
    KnowledgeCollectCandidate, KnowledgeCollectReceipt, KnowledgeCollectTask, KnowledgeCredential, KnowledgeDocument,
    KnowledgeRevision, timestamp,
)
from app.schemas.knowledge import RevisionPayload
from app.services.knowledge import jobs
from app.services.knowledge.collect import catalog, providers
from app.services.knowledge.credentials import catalog as credentials
from app.services.knowledge.documents import revision_values
from app.services.knowledge.network.policy import NetworkError

TRANSIENT = {"fetch_timeout", "fetch_failed", "fetch_busy", "upstream_rate_limited", "upstream_status"}
COMPARE = ("title", "content_hash", "product", "version_scope", "min_version", "max_version")


async def snapshot(db, source_id):
    """最多读取千篇 head 元数据与长度，不在网络等待期间持有正文或数据库写锁。"""
    automatic = exists(select(KnowledgeCollectReceipt.job_id).where(
        KnowledgeCollectReceipt.document_id == KnowledgeDocument.id,
        KnowledgeCollectReceipt.revision_number == KnowledgeDocument.head_revision,
        KnowledgeCollectReceipt.outcome.in_(["new", "updated"]),
    )).correlate(KnowledgeDocument) | exists(select(KnowledgeCollectCandidate.id).where(
        KnowledgeCollectCandidate.document_id == KnowledgeDocument.id,
        KnowledgeCollectCandidate.applied_revision == KnowledgeDocument.head_revision,
    )).correlate(KnowledgeDocument)
    rows = (await db.execute(select(
        KnowledgeDocument.id, KnowledgeDocument.external_id, KnowledgeDocument.version, KnowledgeDocument.head_revision,
        *(getattr(KnowledgeRevision, key) for key in (*COMPARE, "source_url")),
        func.length(KnowledgeRevision.content).label("length"), automatic.label("automatic"),
    ).join(KnowledgeRevision, (KnowledgeRevision.document_id == KnowledgeDocument.id) &
           (KnowledgeRevision.number == KnowledgeDocument.head_revision))
        .where(KnowledgeDocument.source_id == source_id).limit(1001))).all()
    if len(rows) > 1000:
        raise NetworkError("source_scope_too_large")
    return {row.external_id: row for row in rows}


async def complete(sessions, lease, pages, baseline):
    """实际修订、逐页回执和任务终态一起提交，失去回执后的重试不能重复追加。"""
    async with asyncio.timeout(30), sessions() as db:
        await jobs.lock_state(db)
        job = await jobs.owned_job(db, lease)
        if job is None:
            return {"job_id": lease.job_id, "outcome": "lease_lost"}
        task = await db.get(KnowledgeCollectTask, job.id)
        if task is None:
            raise NetworkError("collect_task_missing")
        blocked = ""
        try:
            source, parent = await catalog.active_source(db, job.source_id)
            credential_version = await db.scalar(select(KnowledgeCredential.version).where(
                KnowledgeCredential.source_id == source.id))
            if source.version != task.source_version or (credential_version or 0) != task.credential_version:
                blocked = "source_changed"
            if not await knowledge_enabled(db):
                blocked = "service_disabled"
        except HTTPException:
            blocked = "source_unavailable"
        if job.cancel_requested:
            pages, blocked = [], "cancelled"
        elif blocked:
            pages = [providers.Page("source", flags=[blocked])]
        current = await snapshot(db, job.source_id) if not blocked else {}
        if not blocked and not await jobs.heartbeat(db, lease):
            return {"job_id": lease.job_id, "outcome": "lease_lost"}
        counts, warnings = Counter(), Counter()
        await db.execute(delete(KnowledgeCollectReceipt).where(KnowledgeCollectReceipt.job_id == job.id))
        for page in pages:
            old, observed = current.get(page.external_id), baseline.get(page.external_id)
            outcome, number = "failed", None
            candidate_id = None
            flags = list(page.flags)
            if page.excluded:
                outcome = "excluded"
            elif page.values is not None:
                try:
                    payload = RevisionPayload(**page.values, **json.loads(task.policy))
                    values = revision_values(payload, parent.product)
                except (ValidationError, ValueError, TypeError):
                    flags.append("revision_invalid")
                else:
                    comparable = (*COMPARE, "source_url") if source.connector != "github" else COMPARE
                    if old and all(getattr(old, key) == values[key] for key in comparable):
                        outcome, number = "unchanged", old.head_revision
                    elif ((old is not None) != (observed is not None)
                          or (old and (old.version != observed.version or not old.automatic))):
                        outcome = "protected"
                        flags.append("manual_or_concurrent_change")
                        if old:
                            fingerprint = hashlib.sha256(json.dumps(
                                {key: values[key] for key in comparable}, sort_keys=True,
                            ).encode()).hexdigest()
                            candidate = await db.scalar(select(KnowledgeCollectCandidate).where(
                                KnowledgeCollectCandidate.document_id == old.id,
                                KnowledgeCollectCandidate.document_version == old.version,
                                KnowledgeCollectCandidate.source_version == task.source_version,
                                KnowledgeCollectCandidate.fingerprint == fingerprint,
                            ))
                            if candidate is None:
                                candidate = KnowledgeCollectCandidate(
                                    document_id=old.id, document_version=old.version, source_version=task.source_version,
                                    base_revision=old.head_revision, fingerprint=fingerprint,
                                    payload=json.dumps(payload.model_dump(mode="json")),
                                    flags=json.dumps(page.flags),
                                )
                                db.add(candidate)
                                await db.flush()
                            candidate_id = candidate.id
                    elif old and old.length > 400 and len(payload.content) < old.length * 0.4:
                        flags.append("content_shrank")
                    else:
                        number = old.head_revision + 1 if old else 1
                        if old:
                            document = await db.get(KnowledgeDocument, old.id)
                            document.head_revision, document.version = number, document.version + 1
                            document.updated_at = timestamp()
                            outcome = "updated"
                        else:
                            document = KnowledgeDocument(source_id=source.id, external_id=page.external_id)
                            db.add(document)
                            await db.flush()
                            outcome = "new"
                        db.add(KnowledgeRevision(document_id=document.id, number=number, state="review",
                                                 created_by="knowledge-collect:" + job.id, **values))
                        await db.flush()
                        old = document
            counts[outcome] += 1
            warnings.update(flags)
            db.add(KnowledgeCollectReceipt(
                job_id=job.id, external_id=page.external_id, outcome=outcome, flags=json.dumps(sorted(set(flags))),
                document_id=old.id if old else None, revision_number=number,
                base_revision=observed.head_revision if observed else None, source_commit=page.commit,
                candidate_id=candidate_id,
            ))
        result = {"counts": dict(counts), "flags": dict(warnings)}
        if await jobs.owned_job(db, lease) is None:
            await db.rollback()
            return {"job_id": lease.job_id, "outcome": "lease_lost"}
        task.result = json.dumps(result, sort_keys=True)
        usable = counts["new"] + counts["updated"] + counts["unchanged"]
        retry = not blocked and not usable and counts["failed"] and set(warnings) & TRANSIENT and job.attempts < job.max_attempts
        if blocked == "cancelled":
            job.status, outcome = "cancelled", "cancelled"
        elif retry:
            job.status, outcome = "queued", "collect_retry"
            delay = max(min(15 * 2 ** (job.attempts - 1), 120), max((page.retry_after or 0 for page in pages), default=0))
            job.available_at = time.time() + delay
        elif not usable and not counts["protected"]:
            job.status, outcome = "failed", "collect_blocked"
        else:
            job.status = "succeeded"
            outcome = "collected_partial" if counts["failed"] or counts["protected"] or warnings else "collected"
        job.outcome, job.processed_documents = outcome, counts["new"] + counts["updated"]
        job.updated_at, job.version = timestamp(), job.version + 1
        if job.status in jobs.TERMINAL:
            job.finished_at = timestamp()
            await jobs.release_slot(db, job.id)
        job.lease_token, job.lease_until = "", None
        record(db, "knowledge-worker", "knowledge.collect." + outcome, job.id, result)
        await db.commit()
        return {"job_id": lease.job_id, "outcome": outcome}


async def execute(sessions, lease):
    """出站前固定配置与文档版本；任何已知失败仅保存固定代码，不保存上游正文。"""
    baseline = {}
    try:
        async with sessions() as db:
            await jobs.lock_state(db)
            job = await jobs.owned_job(db, lease)
            if job is None:
                return {"job_id": lease.job_id, "outcome": "lease_lost"}
            task = await db.get(KnowledgeCollectTask, lease.job_id)
            if task is None:
                raise NetworkError("collect_task_missing")
            source, _parent = await catalog.active_source(db, job.source_id)
            if source.version != task.source_version or job.cancel_requested:
                raise NetworkError("source_changed")
            material = await credentials.material(db, source)
            baseline = await snapshot(db, source.id)
            await db.commit()
        pages = await providers.gather(source, material)
    except NetworkError as error:
        pages = [providers.Page("source", flags=[error.code], retry_after=error.retry_after)]
    except HTTPException:
        pages = [providers.Page("source", flags=["credential_unavailable"])]
    return await complete(sessions, lease, pages, baseline)
