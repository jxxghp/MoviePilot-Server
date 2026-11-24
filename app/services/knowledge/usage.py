"""
知识中心调用与命中统计的原子累计和后台汇总查询。

统计只记录调用次数、渠道、文档命中次数与时间戳，不保存任何原始查询词、令牌或知识正文。
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    KnowledgeCollection, KnowledgeDocument, KnowledgeDocumentUsage, KnowledgeRevision,
    KnowledgeSource, KnowledgeUsageDaily, timestamp,
)

logger = logging.getLogger(__name__)


def _utc_day(now: datetime) -> str:
    """统一使用 UTC 自然日分桶，避免多节点时区差异造成统计漂移。"""
    return now.strftime("%Y-%m-%d")


async def _upsert_insert(db: AsyncSession):
    """根据当前连接方言选择支持 ON CONFLICT DO UPDATE 的插入构造器。"""
    connection = await db.connection()
    if connection.dialect.name == "postgresql":
        return postgresql_insert
    if connection.dialect.name == "sqlite":
        return sqlite_insert
    raise ValueError(f"unsupported dialect for knowledge usage upsert: {connection.dialect.name}")


async def _upsert_daily(
    db: AsyncSession,
    *,
    day: str,
    channel: str,
    now_iso: str,
    search_calls: int = 0,
    search_matched_calls: int = 0,
    search_hits: int = 0,
    read_calls: int = 0,
    read_hits: int = 0,
) -> None:
    """按 (day, channel) 原子累加日统计，并发写入时不丢失计数。"""
    insert_fn = await _upsert_insert(db)
    statement = insert_fn(KnowledgeUsageDaily).values(
        day=day,
        channel=channel,
        search_calls=search_calls,
        search_matched_calls=search_matched_calls,
        search_hits=search_hits,
        read_calls=read_calls,
        read_hits=read_hits,
        updated_at=now_iso,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[KnowledgeUsageDaily.day, KnowledgeUsageDaily.channel],
        set_={
            "search_calls": KnowledgeUsageDaily.search_calls + search_calls,
            "search_matched_calls": KnowledgeUsageDaily.search_matched_calls + search_matched_calls,
            "search_hits": KnowledgeUsageDaily.search_hits + search_hits,
            "read_calls": KnowledgeUsageDaily.read_calls + read_calls,
            "read_hits": KnowledgeUsageDaily.read_hits + read_hits,
            "updated_at": now_iso,
        },
    )
    await db.execute(statement)


async def _upsert_document_hits(
    db: AsyncSession,
    document_ids: Iterable[str],
    *,
    now_iso: str,
    search_inc: int = 0,
    read_inc: int = 0,
) -> None:
    """按稳定 document_id 顺序批量原子累加文档命中，防止 PostgreSQL 多行并发死锁。"""
    ids = sorted({item for item in document_ids if item})
    if not ids:
        return
    insert_fn = await _upsert_insert(db)
    total_inc = search_inc + read_inc
    rows = [
        {
            "document_id": doc_id,
            "search_hits": search_inc,
            "read_hits": read_inc,
            "total_hits": total_inc,
            "last_hit_at": now_iso,
        }
        for doc_id in ids
    ]
    statement = insert_fn(KnowledgeDocumentUsage).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[KnowledgeDocumentUsage.document_id],
        set_={
            "search_hits": KnowledgeDocumentUsage.search_hits + statement.excluded.search_hits,
            "read_hits": KnowledgeDocumentUsage.read_hits + statement.excluded.read_hits,
            "total_hits": KnowledgeDocumentUsage.total_hits + statement.excluded.total_hits,
            "last_hit_at": statement.excluded.last_hit_at,
        },
    )
    await db.execute(statement)


async def record_search(
    db: AsyncSession,
    *,
    public: bool,
    status: str,
    document_ids: Iterable[str],
) -> None:
    """记录一次已完成的知识检索调用及返回证据对应的文档命中次数。"""
    now = datetime.now(timezone.utc)
    now_iso = timestamp()
    channel = "agent" if public else "client"
    ids = sorted({item for item in document_ids if item})
    matched = 1 if status in {"matched", "conflicting"} and ids else 0
    try:
        await _upsert_daily(
            db,
            day=_utc_day(now),
            channel=channel,
            now_iso=now_iso,
            search_calls=1,
            search_matched_calls=matched,
            search_hits=len(ids),
        )
        if ids:
            await _upsert_document_hits(db, ids, now_iso=now_iso, search_inc=1)
        await db.commit()
    except (SQLAlchemyError, ValueError):
        # 统计落库失败不能影响已经生成的只读检索结果，也不回显内部异常详情。
        await db.rollback()
        logger.warning("Failed to record knowledge search usage statistics", exc_info=True)


async def record_read(
    db: AsyncSession,
    *,
    public: bool,
    status: str,
    document_id: str | None,
) -> None:
    """记录一次已完成的补读上下文调用及对应文档命中次数。"""
    now = datetime.now(timezone.utc)
    now_iso = timestamp()
    channel = "agent" if public else "client"
    hit = 1 if status == "matched" and document_id else 0
    try:
        await _upsert_daily(
            db,
            day=_utc_day(now),
            channel=channel,
            now_iso=now_iso,
            read_calls=1,
            read_hits=hit,
        )
        if hit and document_id:
            await _upsert_document_hits(db, (document_id,), now_iso=now_iso, read_inc=1)
        await db.commit()
    except (SQLAlchemyError, ValueError):
        await db.rollback()
        logger.warning("Failed to record knowledge read usage statistics", exc_info=True)


def document_usage_data(row: KnowledgeDocumentUsage | None) -> dict:
    """将文档命中记录序列化为固定结构，无命中记录时统一补零。"""
    return {
        "search_hits": int(row.search_hits) if row else 0,
        "read_hits": int(row.read_hits) if row else 0,
        "total_hits": int(row.total_hits) if row else 0,
        "last_hit_at": row.last_hit_at if row else None,
    }


def _empty_channel() -> dict:
    """固定渠道统计结构，前端无需猜测缺省字段。"""
    return {
        "total_calls": 0,
        "search_calls": 0,
        "search_matched_calls": 0,
        "search_hits": 0,
        "read_calls": 0,
        "read_hits": 0,
    }


async def status_counts(db: AsyncSession) -> dict:
    """为知识中心状态栏提供轻量调用与命中汇总。"""
    today = _utc_day(datetime.now(timezone.utc))
    totals = (await db.execute(select(
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls), 0),
        func.coalesce(func.sum(KnowledgeUsageDaily.read_calls), 0),
        func.coalesce(func.sum(KnowledgeUsageDaily.search_hits), 0),
        func.coalesce(func.sum(KnowledgeUsageDaily.read_hits), 0),
    ))).one()
    today_calls = await db.scalar(select(
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls + KnowledgeUsageDaily.read_calls), 0),
    ).where(KnowledgeUsageDaily.day == today))
    hit_documents = await db.scalar(select(func.count()).select_from(KnowledgeDocumentUsage).where(
        KnowledgeDocumentUsage.total_hits > 0,
    ))
    return {
        "total_calls": int(totals[0] + totals[1]),
        "today_calls": int(today_calls or 0),
        "total_hits": int(totals[2] + totals[3]),
        "hit_documents": int(hit_documents or 0),
    }


async def summary(db: AsyncSession, *, days: int = 14, top_limit: int = 20) -> dict:
    """
    聚合知识中心总调用次数、时间窗口、调用渠道、近 N 天日趋势和热门命中知识排行。
    """
    now = datetime.now(timezone.utc)
    today = _utc_day(now)
    day_7 = _utc_day(now - timedelta(days=6))
    day_30 = _utc_day(now - timedelta(days=29))
    day_trend = _utc_day(now - timedelta(days=max(days - 1, 0)))

    # 1. 按渠道聚合总计
    channel_rows = (await db.execute(select(
        KnowledgeUsageDaily.channel,
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls), 0).label("search_calls"),
        func.coalesce(func.sum(KnowledgeUsageDaily.search_matched_calls), 0).label("search_matched_calls"),
        func.coalesce(func.sum(KnowledgeUsageDaily.search_hits), 0).label("search_hits"),
        func.coalesce(func.sum(KnowledgeUsageDaily.read_calls), 0).label("read_calls"),
        func.coalesce(func.sum(KnowledgeUsageDaily.read_hits), 0).label("read_hits"),
    ).group_by(KnowledgeUsageDaily.channel))).all()

    channels = {"agent": _empty_channel(), "client": _empty_channel()}
    for row in channel_rows:
        if row.channel not in channels:
            continue
        search_calls = int(row.search_calls)
        read_calls = int(row.read_calls)
        channels[row.channel] = {
            "total_calls": search_calls + read_calls,
            "search_calls": search_calls,
            "search_matched_calls": int(row.search_matched_calls),
            "search_hits": int(row.search_hits),
            "read_calls": read_calls,
            "read_hits": int(row.read_hits),
        }

    search_calls = sum(item["search_calls"] for item in channels.values())
    search_matched_calls = sum(item["search_matched_calls"] for item in channels.values())
    search_hits = sum(item["search_hits"] for item in channels.values())
    read_calls = sum(item["read_calls"] for item in channels.values())
    read_hits = sum(item["read_hits"] for item in channels.values())

    # 2. 今日 / 近 7 天 / 近 30 天窗口调用次数
    today_calls = await db.scalar(select(
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls + KnowledgeUsageDaily.read_calls), 0),
    ).where(KnowledgeUsageDaily.day == today))
    last_7_days_calls = await db.scalar(select(
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls + KnowledgeUsageDaily.read_calls), 0),
    ).where(KnowledgeUsageDaily.day >= day_7))
    last_30_days_calls = await db.scalar(select(
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls + KnowledgeUsageDaily.read_calls), 0),
    ).where(KnowledgeUsageDaily.day >= day_30))

    # 3. 已命中知识数与当前已发布知识总数
    hit_documents = await db.scalar(select(func.count()).select_from(KnowledgeDocumentUsage).where(
        KnowledgeDocumentUsage.total_hits > 0,
    ))
    published_documents = await db.scalar(select(func.count()).select_from(KnowledgeDocument).where(
        KnowledgeDocument.published_revision.is_not(None),
    ))

    # 4. 近 N 天按日汇总（倒序展示最新日期在前）
    daily_rows = (await db.execute(select(
        KnowledgeUsageDaily.day,
        func.coalesce(func.sum(KnowledgeUsageDaily.search_calls), 0).label("search_calls"),
        func.coalesce(func.sum(KnowledgeUsageDaily.search_matched_calls), 0).label("search_matched_calls"),
        func.coalesce(func.sum(KnowledgeUsageDaily.search_hits), 0).label("search_hits"),
        func.coalesce(func.sum(KnowledgeUsageDaily.read_calls), 0).label("read_calls"),
        func.coalesce(func.sum(KnowledgeUsageDaily.read_hits), 0).label("read_hits"),
    ).where(KnowledgeUsageDaily.day >= day_trend)
     .group_by(KnowledgeUsageDaily.day)
     .order_by(KnowledgeUsageDaily.day.desc()))).all()

    daily = [
        {
            "day": row.day,
            "total_calls": int(row.search_calls) + int(row.read_calls),
            "search_calls": int(row.search_calls),
            "search_matched_calls": int(row.search_matched_calls),
            "search_hits": int(row.search_hits),
            "read_calls": int(row.read_calls),
            "read_hits": int(row.read_hits),
        }
        for row in daily_rows
    ]

    # 5. 热门命中知识排行（按累计命中次数、最近命中时间、文档 ID 稳定排序）
    top_rows = (await db.execute(select(
        KnowledgeDocument.id.label("document_id"),
        KnowledgeDocument.external_id,
        KnowledgeDocument.source_id,
        KnowledgeDocument.published_revision,
        KnowledgeRevision.title,
        KnowledgeSource.name.label("source_name"),
        KnowledgeCollection.name.label("collection_name"),
        KnowledgeCollection.domain,
        KnowledgeDocumentUsage.search_hits,
        KnowledgeDocumentUsage.read_hits,
        KnowledgeDocumentUsage.total_hits,
        KnowledgeDocumentUsage.last_hit_at,
    ).select_from(KnowledgeDocumentUsage)
     .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeDocumentUsage.document_id)
     .join(
         KnowledgeRevision,
         (KnowledgeRevision.document_id == KnowledgeDocument.id)
         & (KnowledgeRevision.number == func.coalesce(
             KnowledgeDocument.published_revision, KnowledgeDocument.head_revision,
         )),
     )
     .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
     .join(KnowledgeCollection, KnowledgeCollection.id == KnowledgeSource.collection_id)
     .where(KnowledgeDocumentUsage.total_hits > 0)
     .order_by(
         KnowledgeDocumentUsage.total_hits.desc(),
         func.coalesce(KnowledgeDocumentUsage.last_hit_at, "").desc(),
         KnowledgeDocument.id.asc(),
     ).limit(top_limit))).all()

    top_documents = [
        {
            "document_id": row.document_id,
            "external_id": row.external_id,
            "title": row.title,
            "source_id": row.source_id,
            "source_name": row.source_name,
            "collection_name": row.collection_name,
            "domain": row.domain,
            "published_revision": row.published_revision,
            "search_hits": int(row.search_hits),
            "read_hits": int(row.read_hits),
            "total_hits": int(row.total_hits),
            "last_hit_at": row.last_hit_at,
        }
        for row in top_rows
    ]

    return {
        "totals": {
            "total_calls": search_calls + read_calls,
            "search_calls": search_calls,
            "search_matched_calls": search_matched_calls,
            "search_hits": search_hits,
            "read_calls": read_calls,
            "read_hits": read_hits,
            "total_hits": search_hits + read_hits,
            "hit_documents": int(hit_documents or 0),
            "published_documents": int(published_documents or 0),
        },
        "windows": {
            "today_calls": int(today_calls or 0),
            "last_7_days_calls": int(last_7_days_calls or 0),
            "last_30_days_calls": int(last_30_days_calls or 0),
        },
        "channels": channels,
        "daily": daily,
        "top_documents": top_documents,
    }
