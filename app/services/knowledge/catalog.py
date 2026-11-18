"""
知识元数据的分页维护、别名唯一性和并发保护。
"""

import json
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.models.knowledge import (
    KnowledgeCollection, KnowledgeCredential, KnowledgeDocument, KnowledgeSite, KnowledgeSiteName, KnowledgeSource, timestamp,
)
from app.schemas.knowledge import (
    CollectionPayload, CollectionUpdate, SitePayload, SiteUpdate, SourcePayload, SourceUpdate, normalized_name,
)
from app.services.knowledge import jobs
from app.services.knowledge.network.policy import BOUND_FIELDS

# 固定表和字段白名单只由内部调用选择，避免未来新增秘密字段时被自动回显。
CatalogKind = Literal["collections", "sites", "sources"]
MODELS = {"collections": KnowledgeCollection, "sites": KnowledgeSite, "sources": KnowledgeSource}
FIELDS = {
    "collections": ("slug", "name", "description", "domain", "product", "enabled"),
    "sites": ("name", "domain", "enabled"),
    "sources": ("collection_id", "site_id", "name", "connector", "url", "repository",
                "branch", "paths", "selector", "refresh_minutes", "enabled"),
}


async def get_row(db: AsyncSession, model, identity: str, *, for_update: bool = False):
    """
    按固定模型读取维护对象，不向客户端泄露其他表或内部异常。
    """
    statement = select(model).where(model.id == identity)
    if for_update:
        statement = statement.with_for_update()
    row = await db.scalar(statement)
    if row is None:
        raise HTTPException(404, "知识记录不存在")
    return row


def metadata(kind: CatalogKind, row) -> dict:
    """
    明确投影可以展示的元数据，不序列化 ORM 内部状态。
    """
    item = {key: getattr(row, key) for key in ("id", "version", "created_at", "updated_at", *FIELDS[kind])}
    if kind == "sources":
        item["paths"] = json.loads(item["paths"])
    return item


async def list_items(db: AsyncSession, kind: CatalogKind, q: str, page: int, size: int,
                     collection_id: str | None = None, site_id: str | None = None,
                     connector: str | None = None) -> dict:
    """
    使用有界分页读取真实记录，别名在同批查询中补全。
    """
    model = MODELS[kind]
    conditions = []
    if q:
        conditions.append(model.name.icontains(q, autoescape=True))
    if kind == "sources" and collection_id:
        conditions.append(model.collection_id == collection_id)
    if kind == "sources" and site_id:
        conditions.append(model.site_id == site_id)
    if kind == "sources" and connector:
        conditions.append(model.connector == connector)
    total = await db.scalar(select(func.count()).select_from(model).where(*conditions))
    rows = list((await db.scalars(select(model).where(*conditions).order_by(model.created_at.desc(), model.id)
                                 .offset((page - 1) * size).limit(size))).all())
    items = [metadata(kind, row) for row in rows]
    if kind == "sites" and items:
        aliases = (await db.scalars(select(KnowledgeSiteName).where(
            KnowledgeSiteName.site_id.in_([row.id for row in rows]), KnowledgeSiteName.kind == "alias",
        ).order_by(KnowledgeSiteName.normalized))).all()
        grouped = {}
        for alias in aliases:
            grouped.setdefault(alias.site_id, []).append(alias.label)
        for item in items:
            item["aliases"] = grouped.get(item["id"], [])
    return {"items": items, "total": total or 0, "page": page, "size": size}


async def read_item(db: AsyncSession, kind: CatalogKind, identity: str) -> dict:
    """表单按 ID 刷新当前版本与身份锁定状态，不从旧分页结果推测可编辑字段。"""
    row = await get_row(db, MODELS[kind], identity)
    item = metadata(kind, row)
    if kind == "sites":
        item["aliases"] = list((await db.scalars(select(KnowledgeSiteName.label).where(
            KnowledgeSiteName.site_id == identity, KnowledgeSiteName.kind == "alias",
        ).order_by(KnowledgeSiteName.normalized))).all())
    if kind in {"collections", "sites"}:
        field = KnowledgeSource.collection_id if kind == "collections" else KnowledgeSource.site_id
        item["has_sources"] = bool(await db.scalar(select(KnowledgeSource.id).where(field == identity).limit(1)))
    else:
        item["has_documents"] = bool(await db.scalar(select(KnowledgeDocument.id).where(
            KnowledgeDocument.source_id == identity).limit(1)))
        item["has_credential"] = bool(await db.scalar(select(KnowledgeCredential.id).where(
            KnowledgeCredential.source_id == identity).limit(1)))
    return item


async def commit(db: AsyncSession) -> None:
    """
    原子提交维护与审计，将标识或别名冲突转换为可处理的冲突响应。
    """
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, "知识标识、文档身份或站点别名已被占用，请刷新后重试") from error


async def replace_values(db: AsyncSession, row, expected_version: int, values: dict) -> None:
    """
    在数据库原子检查版本；失败不会写入审计或覆盖另一位维护者的数据。
    """
    result = await db.execute(update(type(row)).where(
        type(row).id == row.id, type(row).version == expected_version,
    ).values(**values, version=expected_version + 1, updated_at=timestamp())
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "知识记录已被其他操作修改，请刷新后重试")


async def create_collection(db: AsyncSession, payload: CollectionPayload, actor: str) -> dict:
    """
    创建独立集合，初始不授予任何公共或实例读取权限。
    """
    row = KnowledgeCollection(**payload.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, "集合标识已存在") from error
    record(db, actor, "knowledge.collection.create", row.id, {"slug": row.slug})
    await commit(db)
    await db.refresh(row)
    return metadata("collections", row)


async def update_collection(db: AsyncSession, identity: str, payload: CollectionUpdate, actor: str) -> dict:
    """
    更新显示与启停；已有来源的产品和领域不能被静默改写。
    """
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeCollection, identity, for_update=True)
    changed = row.enabled != payload.enabled
    if row.slug != payload.slug:
        raise HTTPException(409, "集合稳定标识不能修改")
    if (row.domain, row.product) != (payload.domain, payload.product):
        if await db.scalar(select(KnowledgeSource.id).where(KnowledgeSource.collection_id == identity).limit(1)):
            raise HTTPException(409, "集合已有来源，不能改变领域或产品归属")
    await replace_values(db, row, payload.expected_version, payload.model_dump(exclude={"expected_version"}))
    if changed:
        await jobs.enqueue_index(db, actor)
    record(db, actor, "knowledge.collection.update", identity, {"enabled": payload.enabled})
    await commit(db)
    await db.refresh(row)
    return metadata("collections", row)


def site_names(identity: str, payload: SitePayload) -> list[KnowledgeSiteName]:
    """
    名称、域名和别名共享规范化唯一键，同站重复称呼只保存一次。
    """
    labels = [(payload.name, "name"), (payload.domain, "domain"), *((item, "alias") for item in payload.aliases)]
    names = {}
    for label, kind in labels:
        normalized = normalized_name(label)
        names.setdefault(normalized, KnowledgeSiteName(normalized=normalized, label=label, kind=kind, site_id=identity))
    return list(names.values())


async def create_site(db: AsyncSession, payload: SitePayload, actor: str) -> dict:
    """
    同一事务创建站点及所有名称，别名冲突时整体回滚。
    """
    from app.models.knowledge import new_identity

    row = KnowledgeSite(id=new_identity(), **payload.model_dump(exclude={"aliases"}))
    db.add(row)
    # 先写入父记录，使 PostgreSQL 的即时外键检查也能成立。
    try:
        await db.flush()
        db.add_all(site_names(row.id, payload))
        record(db, actor, "knowledge.site.create", row.id, {"alias_count": len(payload.aliases)})
        await commit(db)
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, "站点域名已存在") from error
    await db.refresh(row)
    return {**metadata("sites", row), "aliases": [item.label for item in site_names(row.id, payload) if item.kind == "alias"]}


async def update_site(db: AsyncSession, identity: str, payload: SiteUpdate, actor: str) -> dict:
    """
    原子替换本站别名；已绑定来源的主域名保持稳定。
    """
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeSite, identity, for_update=True)
    changed = row.enabled != payload.enabled
    if row.domain != payload.domain:
        if await db.scalar(select(KnowledgeSource.id).where(KnowledgeSource.site_id == identity).limit(1)):
            raise HTTPException(409, "站点已绑定来源，请为新域名建立独立站点")
    await replace_values(db, row, payload.expected_version, payload.model_dump(exclude={"expected_version", "aliases"}))
    await db.execute(delete(KnowledgeSiteName).where(KnowledgeSiteName.site_id == identity))
    db.add_all(site_names(identity, payload))
    if changed:
        await jobs.enqueue_index(db, actor)
    record(db, actor, "knowledge.site.update", identity, {"alias_count": len(payload.aliases), "enabled": payload.enabled})
    await commit(db)
    await db.refresh(row)
    return {**metadata("sites", row), "aliases": [item.label for item in site_names(identity, payload) if item.kind == "alias"]}


async def validate_source_parents(db: AsyncSession, payload: SourcePayload) -> None:
    """
    显式校验来源归属，在未启用外键的 SQLite 环境也不产生悬空记录。
    """
    await get_row(db, KnowledgeCollection, payload.collection_id, for_update=True)
    if payload.site_id:
        await get_row(db, KnowledgeSite, payload.site_id, for_update=True)


def source_values(payload: SourcePayload) -> dict:
    """
    只把已校验的路径列表序列化为跨数据库一致的文本。
    """
    values = payload.model_dump(exclude={"expected_version"})
    values["paths"] = json.dumps(payload.paths, ensure_ascii=False)
    return values


async def create_source(db: AsyncSession, payload: SourcePayload, actor: str) -> dict:
    """
    保存明确来源配置，不在表单保存期间发起网络请求。
    """
    await validate_source_parents(db, payload)
    row = KnowledgeSource(**source_values(payload))
    db.add(row)
    await db.flush()
    record(db, actor, "knowledge.source.create", row.id, {"connector": row.connector})
    await commit(db)
    await db.refresh(row)
    return metadata("sources", row)


async def update_source(db: AsyncSession, identity: str, payload: SourceUpdate, actor: str) -> dict:
    """
    来源已有文档后不改写身份和归属，路径调整仍需后续任务生成新草稿。
    """
    await jobs.lock_state(db)
    row = await get_row(db, KnowledgeSource, identity, for_update=True)
    changed = row.enabled != payload.enabled
    await validate_source_parents(db, payload)
    values = source_values(payload)
    if any(getattr(row, key) != values[key] for key in BOUND_FIELDS):
        if await db.scalar(select(KnowledgeCredential.id).where(KnowledgeCredential.source_id == identity)):
            raise HTTPException(409, "来源已绑定采集凭据，请由超级管理员先清除凭据再更改目标范围")
    fixed = ("collection_id", "site_id", "connector", "url", "repository")
    if any(getattr(row, key) != getattr(payload, key) for key in fixed):
        if await db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.source_id == identity).limit(1)):
            raise HTTPException(409, "来源已有文档，不能改变其身份或集合、站点归属")
    await replace_values(db, row, payload.expected_version, values)
    if changed:
        await jobs.enqueue_index(db, actor)
    record(db, actor, "knowledge.source.update", identity, {"enabled": payload.enabled})
    await commit(db)
    await db.refresh(row)
    return metadata("sources", row)
