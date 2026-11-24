"""
手工知识的稳定身份、草稿追加和私有正文读取。
"""

import hashlib

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.models.knowledge import (
    KnowledgeCollection, KnowledgeDocument, KnowledgeDocumentUsage, KnowledgeRevision, KnowledgeSite, KnowledgeSource,
)
from app.schemas.knowledge import DocumentPayload, RevisionCreate, RevisionPayload
from app.services.knowledge.catalog import commit, get_row, replace_values
from app.services.knowledge import jobs, usage

REVISION_FIELDS = (
    "id", "number", "title", "content_hash", "product", "version_scope", "min_version", "max_version",
    "source_url", "source_commit", "fetched_at", "valid_until", "state", "created_at", "created_by",
)


def revision_values(payload: RevisionPayload, product: str) -> dict:
    """
    保留未知采集时间，分别记录正文摘要与显式的适用性信息。
    """
    values = payload.model_dump(mode="json", exclude={"expected_version", "source_id", "external_id"})
    values["product"] = payload.product or product
    values["content_hash"] = hashlib.sha256(payload.content.encode()).hexdigest()
    return values


def revision_data(row: KnowledgeRevision, *, content: bool = False) -> dict:
    """
    列表不携带正文，详情只在已验证管理员会话内返回。
    """
    item = {key: getattr(row, key) for key in REVISION_FIELDS}
    if content:
        item["content"] = row.content
    return item


def document_data(row: KnowledgeDocument, revision: KnowledgeRevision, *, content: bool = False,
                  hit: KnowledgeDocumentUsage | None = None) -> dict:
    """
    文档当前版本、不可变修订与累计命中统计分开返回，避免误报已发布。
    """
    return {
        "id": row.id, "version": row.version, "source_id": row.source_id, "external_id": row.external_id,
        "head_revision": row.head_revision, "published_revision": row.published_revision,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "usage": usage.document_usage_data(hit),
        "revision": revision_data(revision, content=content),
    }


async def collection_for_source(db: AsyncSession, source: KnowledgeSource, payload: RevisionPayload) -> KnowledgeCollection:
    """
    防止手工正文被标为与集合不同的产品，后续检索不会混用产品分支。
    """
    collection = await get_row(db, KnowledgeCollection, source.collection_id, for_update=True)
    if collection.product and payload.product and collection.product != payload.product:
        raise HTTPException(422, "知识产品与集合归属不一致")
    if payload.version_scope == "range" and not (payload.product or collection.product):
        raise HTTPException(422, "版本范围需要明确的适用产品")
    return collection


async def create_document(db: AsyncSession, payload: DocumentPayload, actor: str) -> dict:
    """
    在手工来源创建第一份草稿，重复外部身份不能覆盖既有文档。
    """
    source = await get_row(db, KnowledgeSource, payload.source_id, for_update=True)
    if source.connector != "manual":
        raise HTTPException(422, "手工新增知识需要使用手工来源")
    collection = await collection_for_source(db, source, payload)
    row = KnowledgeDocument(source_id=source.id, external_id=payload.external_id, head_revision=1)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, "来源内的文档身份已存在") from error
    revision = KnowledgeRevision(document_id=row.id, number=1, created_by=actor, **revision_values(payload, collection.product))
    db.add(revision)
    record(db, actor, "knowledge.document.create", row.id, {"revision": 1})
    await commit(db)
    await db.refresh(row)
    await db.refresh(revision)
    return document_data(row, revision, content=True)


async def append_revision(db: AsyncSession, identity: str, payload: RevisionCreate, actor: str) -> dict:
    """
    先锁知识事实再锁文档，和采集提交保持顺序；旧版与已发布指针保持不变。
    """
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeDocument, identity, for_update=True)
    source = await get_row(db, KnowledgeSource, row.source_id, for_update=True)
    collection = await collection_for_source(db, source, payload)
    number = row.head_revision + 1
    await replace_values(db, row, payload.expected_version, {"head_revision": number})
    revision = KnowledgeRevision(document_id=identity, number=number, created_by=actor,
                                 **revision_values(payload, collection.product))
    db.add(revision)
    record(db, actor, "knowledge.document.revise", identity, {"revision": number})
    await commit(db)
    await db.refresh(row)
    await db.refresh(revision)
    hit = await db.get(KnowledgeDocumentUsage, identity)
    return document_data(row, revision, content=True, hit=hit)


async def list_documents(db: AsyncSession, q: str, page: int, size: int, source_id: str | None,
                         sort: str = "updated_at") -> dict:
    """
    分页展示最新草稿元数据及命中次数，不通过列表传输整篇正文。
    """
    conditions = []
    if q:
        conditions.append(KnowledgeRevision.title.icontains(q, autoescape=True))
    if source_id:
        conditions.append(KnowledgeDocument.source_id == source_id)
    join = (KnowledgeDocument.id == KnowledgeRevision.document_id) & (
        KnowledgeDocument.head_revision == KnowledgeRevision.number)
    base_statement = select(KnowledgeDocument, KnowledgeRevision).join(KnowledgeRevision, join).where(*conditions)
    total = await db.scalar(select(func.count()).select_from(base_statement.subquery()))
    statement = select(KnowledgeDocument, KnowledgeRevision, KnowledgeDocumentUsage).join(
        KnowledgeRevision, join,
    ).outerjoin(
        KnowledgeDocumentUsage, KnowledgeDocumentUsage.document_id == KnowledgeDocument.id,
    ).where(*conditions)
    if sort == "hits":
        # 无命中记录统一视为 0 次与空时间，保证 SQLite 与 PostgreSQL 排序完全一致。
        order = (
            func.coalesce(KnowledgeDocumentUsage.total_hits, 0).desc(),
            func.coalesce(KnowledgeDocumentUsage.last_hit_at, "").desc(),
            KnowledgeDocument.updated_at.desc(),
            KnowledgeDocument.id,
        )
    else:
        order = (KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
    rows = (await db.execute(statement.order_by(*order).offset((page - 1) * size).limit(size))).all()
    return {"items": [document_data(document, revision, hit=hit) for document, revision, hit in rows],
            "total": total or 0, "page": page, "size": size}


async def read_document(db: AsyncSession, identity: str, number: int | None = None) -> dict:
    """
    读取最新或明确历史修订及该文档累计命中统计，历史原文不会被新草稿替换。
    """
    row = await get_row(db, KnowledgeDocument, identity)
    revision = await db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == identity, KnowledgeRevision.number == (number or row.head_revision),
    ))
    if revision is None:
        raise HTTPException(404, "知识修订不存在")
    hit = await db.get(KnowledgeDocumentUsage, identity)
    return document_data(row, revision, content=True, hit=hit)


async def publish_document(db: AsyncSession, identity: str, expected_version: int, number: int, actor: str) -> dict:
    """
    在同一事务批准明确修订、保留历史并排索引任务，成功不代表快照已经上线。
    """
    state = await jobs.lock_state(db)
    row = await get_row(db, KnowledgeDocument, identity, for_update=True)
    source = await get_row(db, KnowledgeSource, row.source_id, for_update=True)
    collection = await get_row(db, KnowledgeCollection, source.collection_id, for_update=True)
    site = await get_row(db, KnowledgeSite, source.site_id) if source.site_id else None
    if not source.enabled or not collection.enabled or (site is not None and not site.enabled):
        raise HTTPException(409, "请先启用知识来源、集合和所属站点")
    revision = await db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == identity, KnowledgeRevision.number == number,
    ))
    if revision is None:
        raise HTTPException(404, "知识修订不存在")
    if row.version != expected_version:
        raise HTTPException(409, "知识记录已被其他操作修改，请刷新后重试")
    if row.published_revision == number:
        hit = await db.get(KnowledgeDocumentUsage, identity)
        return {"document": document_data(row, revision, hit=hit), "job": None,
                "index_pending": not state.active_snapshot or state.active_generation != state.generation}
    if row.published_revision is not None:
        previous = await db.scalar(select(KnowledgeRevision).where(
            KnowledgeRevision.document_id == identity, KnowledgeRevision.number == row.published_revision,
        ))
        previous.state = "superseded"
    revision.state = "published"
    await replace_values(db, row, expected_version, {"published_revision": number})
    await db.flush()
    job = await jobs.enqueue_index(db, actor)
    record(db, actor, "knowledge.document.publish", identity, {"revision": number})
    await db.refresh(row)
    hit = await db.get(KnowledgeDocumentUsage, identity)
    result = {"document": document_data(row, revision, hit=hit), "job": jobs.job_data(job), "index_pending": True}
    await commit(db)
    return result


async def withdraw_document(db: AsyncSession, identity: str, expected_version: int, actor: str) -> dict:
    """
    立即清除 SQL 发布指针并请求重建；即使旧快照仍在，检索也须拒绝已撤回内容。
    """
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeDocument, identity, for_update=True)
    number = row.published_revision
    if row.version != expected_version:
        raise HTTPException(409, "知识记录已被其他操作修改，请刷新后重试")
    revision = await db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == identity, KnowledgeRevision.number == (number or row.head_revision),
    ))
    hit = await db.get(KnowledgeDocumentUsage, identity)
    if number is None:
        return {"document": document_data(row, revision, hit=hit), "job": None, "index_pending": False}
    revision.state = "withdrawn"
    await replace_values(db, row, expected_version, {"published_revision": None})
    await db.flush()
    job = await jobs.enqueue_index(db, actor)
    record(db, actor, "knowledge.document.withdraw", identity, {"revision": number})
    await db.refresh(row)
    result = {"document": document_data(row, revision, hit=hit), "job": jobs.job_data(job), "index_pending": True}
    await commit(db)
    return result
