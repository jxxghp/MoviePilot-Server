"""
导入批次管理与受控原始流上传，复用真实管理员会话和任务协议。
"""

import asyncio
import contextlib
import hashlib
import json
import os
import time

from fastapi import HTTPException, Request
from sqlalchemy import func, or_, select, update

from app.admin.audit import record
from app.models.knowledge import (
    KnowledgeImport, KnowledgeImportReceipt, KnowledgeImportTask, KnowledgeJob,
    KnowledgeJobSlot, KnowledgeRevision, KnowledgeSource, timestamp,
)
from app.schemas.knowledge import ImportApproval, ImportPreflight
from app.services.knowledge import jobs
from app.services.knowledge.catalog import commit, get_row
from app.services.knowledge.checks import content_flags
from app.services.knowledge.imports import files, plans

BATCH_FIELDS = ("id", "version", "source_id", "source_version", "status", "file_hash", "size_bytes",
                "plan_hash", "error_code", "current_job_id", "expires_at", "created_by", "created_at", "updated_at")
PENDING = {"uploading", "uploaded", "upload_failed", "preflight_queued", "ready", "blocked",
           "apply_queued", "cancelled", "failed"}
UPLOAD_TIMEOUT_SECONDS = 300


def batch_data(row) -> dict:
    """
    固定投影不包含文件路径、映射中可能的敏感原值或候选正文。
    """
    return {**{key: getattr(row, key) for key in BATCH_FIELDS},
            **{key: json.loads(getattr(row, key)) for key in ("summary", "result")}}


async def cleanup(db, directory) -> None:
    """
    释放已导入暂存与过期批次；仍有有效运行租约时不删除其正在读取的文件。
    """
    await jobs.lock_state(db)
    rows = (await db.scalars(select(KnowledgeImport).where(
        KnowledgeImport.files_cleared.is_(False),
        or_(KnowledgeImport.status.in_(["imported", "discarded", "expired"]), KnowledgeImport.expires_at <= time.time()),
    ).limit(100).with_for_update())).all()
    removable = []
    clock = time.time()
    for row in rows:
        if row.status in {"imported", "discarded", "expired"}:
            removable.append(row.id)
            continue
        if row.expires_at > clock:
            continue
        job = await db.get(KnowledgeJob, row.current_job_id) if row.current_job_id else None
        if job and job.status == "running" and (job.lease_until or 0) > clock:
            continue
        if job and job.status not in jobs.TERMINAL:
            job.status, job.outcome, job.finished_at = "failed", "expired", timestamp()
            job.lease_token, job.lease_until, job.version = "", None, job.version + 1
            await jobs.release_slot(db, job.id)
        row.status, row.version, row.updated_at = "expired", row.version + 1, timestamp()
        removable.append(row.id)
    await db.commit()
    for identity in removable:
        files.remove_batch(directory, identity)
        await db.execute(update(KnowledgeImport).where(KnowledgeImport.id == identity).values(files_cleared=True))
    await db.commit()


async def upload(db, directory, request: Request, source_id: str, source_version: int, actor: str) -> dict:
    """
    鉴权后接收有界 NDJSON 原始流，不使用会提前在系统临时目录落盘的 multipart。
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-ndjson" or request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(415, "仅接受未压缩的 application/x-ndjson")
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "上传长度无效") from None
    if declared < 0 or declared > files.MAX_BYTES:
        raise HTTPException(413, "知识上传超过大小限制")
    await cleanup(db, directory)
    await jobs.lock_state(db)
    source = await get_row(db, KnowledgeSource, source_id, for_update=True)
    if source.connector != "jsonl" or source.version != source_version:
        raise HTTPException(409, "请选择当前版本的 JSONL 来源")
    pending = await db.scalar(select(func.count()).select_from(KnowledgeImport).where(
        KnowledgeImport.status.in_(list(PENDING)),
    ))
    if pending >= files.MAX_BATCHES:
        raise HTTPException(429, "待处理导入批次已达上限，请先完成或丢弃旧批次")
    try:
        files.check_quota(directory)
    except OSError:
        raise HTTPException(507, "私有暂存空间不足") from None
    row = KnowledgeImport(source_id=source_id, source_version=source_version, created_by=actor,
                          expires_at=time.time() + UPLOAD_TIMEOUT_SECONDS)
    db.add(row)
    await db.flush()
    await db.commit()
    identity = row.id
    temporary = directory / (identity + "-uploading-" + identity + ".jsonl")
    destination = files.path(directory, identity)
    activation_attempted = False
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        size, digest = 0, hashlib.sha256()
        with os.fdopen(descriptor, "wb") as stream:
            async with asyncio.timeout(UPLOAD_TIMEOUT_SECONDS):
                async for block in request.stream():
                    size += len(block)
                    if size > files.MAX_BYTES:
                        raise HTTPException(413, "知识上传超过大小限制")
                    digest.update(block)
                    stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        if size == 0:
            raise HTTPException(422, "知识上传不能为空")
        os.chmod(temporary, 0o400)
        temporary.rename(destination)
        saved = await db.execute(update(KnowledgeImport).where(
            KnowledgeImport.id == identity, KnowledgeImport.version == 1, KnowledgeImport.status == "uploading",
        ).values(status="uploaded", size_bytes=size, file_hash=digest.hexdigest(), version=2, updated_at=timestamp(),
                 expires_at=time.time() + files.STAGING_SECONDS).execution_options(synchronize_session=False))
        if saved.rowcount != 1:
            raise HTTPException(409, "上传租约已改变，请刷新批次状态")
        await db.refresh(row)
        record(db, actor, "knowledge.import.upload", row.id, {"bytes": size})
        activation_attempted = True
        await commit(db)
        return batch_data(row)
    except BaseException as error:
        temporary.unlink(missing_ok=True)
        if not activation_attempted:
            destination.unlink(missing_ok=True)
            await db.rollback()
            current = await db.get(KnowledgeImport, identity, populate_existing=True)
            if current.status == "uploading":
                current.status, current.version = "upload_failed", current.version + 1
            await db.commit()
        if isinstance(error, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(408 if isinstance(error, TimeoutError) else 503, "知识暂存上传未完成") from None


async def list_batches(db, page: int, size: int) -> dict:
    """
    分页展示批次元数据，原文件和计划不能从此接口下载。
    """
    total = await db.scalar(select(func.count()).select_from(KnowledgeImport))
    rows = (await db.scalars(select(KnowledgeImport).order_by(KnowledgeImport.created_at.desc(), KnowledgeImport.id)
                             .offset((page - 1) * size).limit(size))).all()
    return {"items": [batch_data(row) for row in rows], "total": total or 0, "page": page, "size": size}


async def require_batch(db, identity: str, version: int | None = None):
    """
    复核批次并发版本和暂存期限，终态回执不受文件到期影响。
    """
    row = await get_row(db, KnowledgeImport, identity, for_update=True)
    if version is not None and row.version != version:
        raise HTTPException(409, "导入批次已改变，请刷新后重试")
    if row.status in {"discarded", "expired"} or (row.status != "imported" and row.expires_at <= time.time()):
        raise HTTPException(410, "导入暂存已丢弃或到期")
    return row


async def queue(db, directory, identity: str, payload: ImportPreflight | ImportApproval, actor: str, *, phase: str) -> dict:
    """
    以固定来源槽排队预检或明确导入；所审阅版本与决定和任务在同一事务保存。
    """
    await jobs.lock_state(db)
    row = await require_batch(db, identity, payload.expected_version)
    allowed = {"uploaded", "ready", "blocked", "cancelled", "failed"} if phase == "preflight" else {"ready"}
    if row.status not in allowed:
        raise HTTPException(409, "当前批次状态不允许此操作")
    source = await get_row(db, KnowledgeSource, row.source_id, for_update=True)
    if source.connector != "jsonl":
        raise HTTPException(409, "批次来源已改变")
    slot_key = "source:" + source.id
    slot = await db.get(KnowledgeJobSlot, slot_key)
    if slot:
        active = await db.get(KnowledgeJob, slot.job_id)
        if active and active.status not in jobs.TERMINAL:
            raise HTTPException(409, "此来源已有活动任务，请先等待或取消")
        await db.delete(slot)
        await db.flush()
    if phase == "preflight":
        if "suspected_secret" in content_flags(plans.encoded(payload.model_dump())):
            raise HTTPException(422, "字段映射疑似包含秘密，请仅填写来源身份与范围")
        if source.version != payload.expected_source_version:
            raise HTTPException(409, "来源已改变，请刷新并重新预检")
        row.source_version = source.version
        row.mapping = plans.encoded(payload.model_dump(mode="json", exclude={"expected_version"}))
        row.plan_id, row.plan_hash, row.summary = "", "", "{}"
        row.error_code = ""
    else:
        if source.version != row.source_version:
            raise HTTPException(409, "来源已改变，请重新预检")
        validate_approval(directory, row, payload)
        row.confirmation = plans.encoded(payload.model_dump(exclude={"expected_version"}))
    job = KnowledgeJob(kind="import", source_id=source.id, available_at=time.time(), created_by=actor)
    db.add(job)
    await db.flush()
    db.add(KnowledgeImportTask(job_id=job.id, import_id=row.id, phase=phase))
    db.add(KnowledgeJobSlot(key=slot_key, job_id=job.id))
    row.status, row.current_job_id = ("preflight_queued" if phase == "preflight" else "apply_queued"), job.id
    row.version, row.updated_at = row.version + 1, timestamp()
    record(db, actor, "knowledge.import." + phase, row.id, {"job": job.id})
    await db.flush()
    result = {**batch_data(row), "job": jobs.job_data(job)}
    await commit(db)
    return result


def validate_approval(directory, batch, approval: ImportApproval) -> None:
    """
    警告必须确认，阻止行只能明确跳过，人工冲突必须逐行选择，不能默许覆盖。
    """
    skip, append = set(approval.skip_lines), set(approval.append_lines)
    with contextlib.closing(plans.open_plan(directory, batch)) as plan:
        rows = plan.execute("SELECT line,action,flags FROM rows").fetchall()
    by_line = {row["line"]: row for row in rows}
    if not (skip | append) <= by_line.keys() or any(by_line[line]["action"] != "conflict" for line in append):
        raise HTTPException(422, "行级决定不属于当前计划")
    if any(row["action"] == "blocked" and row["line"] not in skip for row in rows):
        raise HTTPException(409, "阻止行必须明确跳过，或修正文件后重新上传")
    if any(row["action"] == "conflict" and row["line"] not in skip | append for row in rows):
        raise HTTPException(409, "人工修订冲突需要逐行确认追加或跳过")
    if any(json.loads(row["flags"]) for row in rows) and not approval.acknowledge_warnings:
        raise HTTPException(409, "请先核对并确认预检警告")
    if not any(row["action"] in {"new", "updated", "unchanged", "conflict"} and row["line"] not in skip for row in rows):
        raise HTTPException(409, "没有明确选择的可导入记录")


async def rows(db, directory, identity: str, page: int, size: int, line: int | None = None) -> dict:
    """
    按页展示计划或已提交行回执；正文只在明确单行详情中展示，阻止行不回显。
    """
    batch = await require_batch(db, identity)
    if batch.status == "imported":
        statement = select(KnowledgeImportReceipt).where(KnowledgeImportReceipt.import_id == identity)
        if line is not None:
            statement = statement.where(KnowledgeImportReceipt.line == line)
        total = await db.scalar(select(func.count()).select_from(statement.subquery()))
        receipts = (await db.scalars(statement.order_by(KnowledgeImportReceipt.line)
                                     .offset((page - 1) * size).limit(size))).all()
        result = []
        for receipt in receipts:
            item = {"line": receipt.line, "document_id": receipt.document_id, "revision": receipt.revision_number,
                    "action": receipt.outcome, "flags": json.loads(receipt.flags)}
            if line is not None:
                for label, number in (("candidate", receipt.revision_number), ("base", receipt.base_revision)):
                    revision = await db.scalar(select(KnowledgeRevision).where(
                        KnowledgeRevision.document_id == receipt.document_id, KnowledgeRevision.number == number,
                    )) if number else None
                    item[label] = {"title": revision.title, "content": revision.content} if revision else None
            result.append(item)
    elif batch.status in {"ready", "blocked"} and batch.plan_id:
        with contextlib.closing(plans.open_plan(directory, batch)) as plan:
            total = plan.execute("SELECT count(*) FROM rows").fetchone()[0] if line is None else 1
            query = "SELECT * FROM rows WHERE line=?" if line is not None else "SELECT * FROM rows ORDER BY line LIMIT ? OFFSET ?"
            candidates = plan.execute(query, (line,) if line is not None else (size, (page - 1) * size)).fetchall()
        result = [plans.row_data(row) for row in candidates]
        if line is not None and candidates:
            candidate = candidates[0]
            result[0]["candidate"] = json.loads(candidate["payload"]) if candidate["payload"] else None
            revision = await db.scalar(select(KnowledgeRevision).where(
                KnowledgeRevision.document_id == candidate["document_id"],
                KnowledgeRevision.number == candidate["base_revision"],
            )) if candidate["base_revision"] else None
            result[0]["base"] = {"title": revision.title, "content": revision.content} if revision else None
    else:
        raise HTTPException(409, "此批次暂无可复核计划或回执")
    if line is not None:
        if not result:
            raise HTTPException(404, "导入记录不存在")
        return result[0]
    return {"items": result, "total": total or 0, "page": page, "size": size}


async def discard(db, directory, identity: str, version: int, actor: str) -> dict:
    """
    非活动批次可明确丢弃私有暂存；已提交修订和无正文回执不会被删除。
    """
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeImport, identity, for_update=True)
    if row.version != version:
        raise HTTPException(409, "导入批次已改变，请刷新后重试")
    job = await db.get(KnowledgeJob, row.current_job_id) if row.current_job_id else None
    if row.status == "uploading" or (job and job.status not in jobs.TERMINAL):
        raise HTTPException(409, "请先取消活动任务，或等待上传结束")
    if row.status != "imported":
        row.status, row.version, row.updated_at = "discarded", row.version + 1, timestamp()
    record(db, actor, "knowledge.import.discard", row.id)
    result = batch_data(row)
    await commit(db)
    files.remove_batch(directory, row.id)
    return result
