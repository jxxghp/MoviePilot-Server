"""
有界线程中的只读 SQLite 查询及快照退役；不在请求内重建或全库扫描。
"""

import asyncio
import contextlib
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.models.knowledge import KnowledgeJob, KnowledgeState
from app.services.knowledge import indexes, jobs

THREADS = 4
QUERY_SECONDS = 2.5
GRACE_SECONDS = 10
BUILD_GRACE_SECONDS = 1200
_pool = ThreadPoolExecutor(max_workers=THREADS, thread_name_prefix="knowledge-read")
_capacity = threading.BoundedSemaphore(THREADS)


class SnapshotMissing(FileNotFoundError):
    """
    SQL 指针读取到文件打开之间发生切换时，允许重新读取当前指针一次。
    """


def snapshot_path(directory: Path, identity: str) -> Path:
    """
    即使数据库损坏也不将任意路径拼进只读 SQLite URI。
    """
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("invalid_snapshot_identity")
    return directory / (identity + ".sqlite")


def connect(directory: Path, identity: str):
    """
    校验不可变快照格式与完整标记，拒绝符号链接和旧格式。
    """
    location = snapshot_path(directory, identity)
    if location.is_symlink():
        raise ValueError("invalid_snapshot")
    if not location.is_file():
        raise SnapshotMissing()
    db = sqlite3.connect(location.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
    try:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata.get("complete") != "1" or metadata.get("format_version") != str(indexes.FORMAT_VERSION):
            raise ValueError("snapshot_rebuild_required")
    except BaseException:
        db.close()
        raise
    db.row_factory = sqlite3.Row
    return db


async def bounded(function, *arguments, **parameters):
    """
    容量随真实线程完成才释放；请求取消不能造成还在工作的线程无限堆积。
    """
    if not _capacity.acquire(blocking=False):
        raise HTTPException(503, "knowledge_busy", headers={"Retry-After": "1"})
    try:
        future = _pool.submit(function, *arguments, **parameters)
    except BaseException:
        _capacity.release()
        raise
    future.add_done_callback(lambda _future: _capacity.release())
    wrapped = asyncio.wrap_future(future)

    def consume(done):
        """
        调用方取消或超时后仍取走线程异常，避免无人领取的 Future 异常日志。
        """
        if not done.cancelled():
            done.exception()

    wrapped.add_done_callback(consume)
    try:
        return await asyncio.wait_for(asyncio.shield(wrapped), timeout=QUERY_SECONDS + 0.5)
    except TimeoutError:
        raise HTTPException(503, "knowledge_query_timeout", headers={"Retry-After": "1"}) from None


def candidates(directory, snapshot, expression, collections, spec, site_id, *, broad=False):
    """
    在候选截断前过滤产品、版本和站点；PT 泛查可把站点资料作为示例候选。
    """
    deadline = time.monotonic() + QUERY_SECONDS
    with contextlib.closing(connect(directory, snapshot)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        conditions = ["search MATCH ?", "c.collection_id IN (" + ",".join("?" for _item in collections) + ")"]
        params = [expression, *collections]
        conditions.append("c.product IN ('',?)")
        params.append(spec.product)
        if spec.domain != "pt" or site_id is not None:
            conditions.append("(c.site_id IS NULL OR c.site_id=?)" if site_id else "c.site_id IS NULL")
            if site_id:
                params.append(site_id)
        if spec.purpose != "development":
            conditions.append("c.kind != 'test'")
        if not broad:
            conditions.append("(c.valid_until IS NULL OR c.valid_until>?)")
            params.append(time.time())
            if spec.product_version is None:
                conditions.append("c.version_scope IN ('all','unknown')")
            else:
                conditions.append("(c.version_scope IN ('all','unknown') OR "
                                  "((c.min_key IS NULL OR c.min_key<=?) AND (c.max_key IS NULL OR c.max_key>=?)))")
                params += [indexes.version_key(spec.product_version)] * 2
        order = ("CASE c.kind WHEN 'reference' THEN 0 WHEN 'guide' THEN 1 ELSE 2 END"
                 if spec.purpose == "development" else "CASE c.kind WHEN 'guide' THEN 0 ELSE 1 END")
        result = db.execute(
            "SELECT c.document_id,c.revision_number,c.chunk_number,c.start_offset,c.end_offset,c.kind,"
            "bm25(search,4.0,1.0) AS relevance FROM search JOIN chunks c ON c.id=search.rowid WHERE "
            + " AND ".join(conditions) + " ORDER BY " + order + ",relevance,c.document_id,c.chunk_number LIMIT 512",
            params,
        ).fetchall()
        return [dict(row) for row in result]


async def collect(db, directory: Path) -> int:
    """
    当前指针与删除决策处于同一锁内；SQL 不可用不删除，活跃及近期文件保留。
    """
    await jobs.lock_state(db)
    state = await db.get(KnowledgeState, "main", populate_existing=True)
    clock = time.time()
    building = await db.scalar(select(KnowledgeJob.id).where(
        KnowledgeJob.kind == "index", KnowledgeJob.status == "running", KnowledgeJob.lease_until > clock,
    ).limit(1))
    entries = []
    for location in directory.iterdir():
        if location.is_symlink() or not location.is_file():
            continue
        if re.fullmatch(r"[a-f0-9]{32}\.sqlite", location.name):
            entries.append(location)
    entries.sort(key=lambda location: location.stat().st_mtime, reverse=True)
    keep = {location.name for location in entries[:2]}
    if state.active_snapshot:
        keep.add(state.active_snapshot + ".sqlite")
    removed = 0
    for location in entries:
        if not building and location.name not in keep and clock - location.stat().st_mtime > GRACE_SECONDS:
            location.unlink(missing_ok=True)
            removed += 1
    for location in directory.iterdir():
        if (re.fullmatch(r"\.building-[a-f0-9]{32}\.sqlite(?:-journal)?", location.name)
                and not location.is_symlink() and location.is_file()
                and clock - location.stat().st_mtime > BUILD_GRACE_SECONDS):
            location.unlink(missing_ok=True)
            removed += 1
    await db.commit()
    return removed
