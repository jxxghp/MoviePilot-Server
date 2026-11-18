"""
手工知识的稳定身份、草稿追加和私有正文读取。
"""

import hashlib

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.models.knowledge import KnowledgeCollection, KnowledgeDocument, KnowledgeRevision, KnowledgeSource
from app.schemas.knowledge import DocumentPayload, RevisionCreate, RevisionPayload
from app.services.knowledge.catalog import commit, get_row, replace_values

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


def document_data(row: KnowledgeDocument, revision: KnowledgeRevision, *, content: bool = False) -> dict:
    """
    文档当前版本与不可变修订分开返回，避免误报已发布。
    """
    return {
        "id": row.id, "version": row.version, "source_id": row.source_id, "external_id": row.external_id,
        "head_revision": row.head_revision, "published_revision": row.published_revision,
        "created_at": row.created_at, "updated_at": row.updated_at,
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
    用文档并发版本追加草稿；旧版正文与已发布指针保持不变。
    """
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
    return document_data(row, revision, content=True)


async def list_documents(db: AsyncSession, q: str, page: int, size: int, source_id: str | None) -> dict:
    """
    分页展示最新草稿元数据，不通过列表传输整篇正文。
    """
    conditions = []
    if q:
        conditions.append(KnowledgeRevision.title.icontains(q, autoescape=True))
    if source_id:
        conditions.append(KnowledgeDocument.source_id == source_id)
    join = (KnowledgeDocument.id == KnowledgeRevision.document_id) & (
        KnowledgeDocument.head_revision == KnowledgeRevision.number)
    statement = select(KnowledgeDocument, KnowledgeRevision).join(KnowledgeRevision, join).where(*conditions)
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (await db.execute(statement.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
                             .offset((page - 1) * size).limit(size))).all()
    return {"items": [document_data(document, revision) for document, revision in rows],
            "total": total or 0, "page": page, "size": size}


async def read_document(db: AsyncSession, identity: str, number: int | None = None) -> dict:
    """
    读取最新或明确历史修订，历史原文不会被新草稿替换。
    """
    row = await get_row(db, KnowledgeDocument, identity)
    revision = await db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == identity, KnowledgeRevision.number == (number or row.head_revision),
    ))
    if revision is None:
        raise HTTPException(404, "知识修订不存在")
    return document_data(row, revision, content=True)
