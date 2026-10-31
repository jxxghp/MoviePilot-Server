"""后台业务数据查询及受控删除，不复用有计数副作用的公共 fork 接口。"""

import json
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.admin.invalidation import admin_cache_invalidation
from app.core.config import settings
from app.core.media import normalize_media_source
from app.db.redis import get_redis
from app.models import (
    PluginRating, PluginRatingSummary, PluginStatistics, SubscribeShare,
    SubscribeStatistics, UsageStatistics, WorkflowShare,
)
from app.models.admin import AdminAudit, AdminUser
from app.services.plugin_rating import PluginRatingService
from app.services.usage_statistic import UsageService

# 表和排序字段必须来自服务端白名单，不能将 URL 参数作为 SQL 标识符。
RESOURCES = {
    "subscriptions": SubscribeStatistics, "subscribe-shares": SubscribeShare,
    "workflow-shares": WorkflowShare, "plugins": PluginStatistics,
    "ratings": PluginRating, "installations": UsageStatistics, "audit": AdminAudit,
}


def model_for(resource: str):
    """仅解析允许管理的业务资源。"""
    if resource not in RESOURCES:
        raise HTTPException(404, "资源不存在")
    return RESOURCES[resource]


def serialize(item) -> dict:
    """过滤历史执行上下文并对实例标识脱敏，保持评分数值可用于页面排序。"""
    data = {}
    for column in item.__table__.columns:
        value = getattr(item, column.name)
        if column.name in {"context", "owner_token_hash"}:
            continue
        if column.name in {"user_uid", "share_uid"} and value:
            value = f"{str(value)[:4]}…{str(value)[-4:]}" if len(str(value)) > 8 else "***"
        if isinstance(value, Decimal):
            value = float(value)
        data[column.name] = value
    return data


async def list_data(db: AsyncSession, resource: str, q: str, page: int, size: int,
                    sort: str, descending: bool, media_type: str, source: str) -> dict:
    """数据库分页、计数及筛选；稳定的主键次序避免相同排序值跨页漂移。"""
    model = model_for(resource)
    query = select(model)
    if q:
        fields = [getattr(model, name) for name in (
            "name", "share_title", "share_user", "plugin_id", "user_uid", "actor", "action", "target", "backend_version",
        ) if hasattr(model, name)]
        # contains(autoescape) 使百分号和下划线按字面搜索。
        query = query.where(or_(*(cast(field, String).icontains(q, autoescape=True) for field in fields)))
    if media_type and hasattr(model, "type"):
        query = query.where(model.type == media_type)
    if source and hasattr(model, "media_source"):
        normalized = normalize_media_source(source)
        if not normalized:
            raise HTTPException(422, "媒体来源无效")
        query = query.where(model.media_source == str(normalized))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    allowed_sort = {
        column.name for column in model.__table__.columns
    } - {"context", "owner_token_hash", "actions", "flows", "detail"}
    if sort and sort not in allowed_sort:
        raise HTTPException(422, "排序字段无效")
    order = getattr(model, sort or "id")
    query = query.order_by(order.desc() if descending else order.asc(), model.id.desc())
    items = [serialize(item) for item in (await db.scalars(query.offset((page - 1) * size).limit(size))).all()]
    if resource == "plugins" and items:
        keys = {item["plugin_id"].lower() for item in items if item.get("plugin_id")}
        summaries = (await db.scalars(select(PluginRatingSummary).where(PluginRatingSummary.plugin_id.in_(keys)))).all()
        mapped = {row.plugin_id: row for row in summaries}
        for item in items:
            summary = mapped.get((item.get("plugin_id") or "").lower())
            item.update(average_rating=float(summary.average_rating) if summary else 0, rating_count=summary.rating_count if summary else 0)
    filters = {}
    for name, label in (("type", "types"), ("media_source", "sources")):
        if hasattr(model, name):
            field = getattr(model, name)
            filters[label] = list((await db.scalars(select(field).where(field.is_not(None), field != "").distinct().order_by(field).limit(1000))).all())
    return {"items": items, "total": total or 0, "page": page, "size": size, "filters": filters}


async def detail(db: AsyncSession, resource: str, item_id: int) -> dict:
    """直接读取详情，不增加分享复用次数。"""
    item = await db.get(model_for(resource), item_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return serialize(item)


async def delete_data(db: AsyncSession, resource: str, item_id: int, actor: AdminUser) -> None:
    """删除指定业务记录；评分删除与汇总重算在同一事务中完成。"""
    if resource == "audit":
        raise HTTPException(403, "操作记录不允许删除")
    item = await db.get(model_for(resource), item_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    if resource == "ratings":
        # 与公共评分写入保持相同锁顺序，串行重算同一插件的汇总。
        summary = await PluginRatingSummary.read(db, item.plugin_id, for_update=True)
        if summary is None:
            summary = PluginRatingSummary(plugin_id=item.plugin_id, average_rating=0, rating_count=0, updated_at="")
            db.add(summary)
            await db.flush()
        await db.delete(item)
        await db.flush()
        average, count = await PluginRating.aggregate(db, item.plugin_id)
        summary.average_rating = PluginRatingService._round_rating(Decimal(str(average or 0)))
        summary.rating_count = count
        summary.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        await db.delete(item)
    record(db, actor, "data.delete", f"{resource}/{item_id}")
    await db.commit()
    await admin_cache_invalidation.invalidate()


async def overview(db: AsyncSession) -> dict:
    """聚合已有统计与最近共享；不伪造尚无历史快照的趋势数据。"""
    usage = await UsageService.get_statistics(db)
    recent = []
    for model, kind in ((SubscribeShare, "subscribe-shares"), (WorkflowShare, "workflow-shares")):
        rows = (await db.scalars(select(model).order_by(model.date.desc(), model.id.desc()).limit(4))).all()
        recent.extend({**serialize(row), "resource": kind} for row in rows)
    recent.sort(key=lambda row: row.get("date") or "", reverse=True)
    return {"usage": usage, "recent_shares": recent[:4]}


async def list_recognition(cursor: int, q: str) -> dict:
    """限量 SCAN 共享识别命名空间，返回游标而非阻塞 Redis 的 KEYS 查询。"""
    redis = get_redis()
    prefix = settings.media_recognize_share_redis_prefix + ":item:"
    items = []
    for _ in range(5):
        cursor, keys = await redis.scan(cursor=cursor, match=prefix + "*", count=100)
        if keys:
            values = await redis.mget(keys)
            for key, raw in zip(keys, values):
                key = key.decode() if isinstance(key, bytes) else key
                if q and q.lower() not in key[len(prefix):].lower():
                    continue
                try:
                    value = json.loads(raw) if raw else None
                except (ValueError, TypeError):
                    value = {"error": "缓存内容无法解析"}
                if value is not None:
                    items.append({"key": key, "data": value})
        if not cursor or len(items) >= 100:
            break
    return {"items": items, "next_cursor": cursor}


async def delete_recognition(db: AsyncSession, key: str, actor: AdminUser) -> None:
    """精确删除一条共享识别缓存，禁止指定其他 Redis 业务命名空间。"""
    if not key.startswith(settings.media_recognize_share_redis_prefix + ":item:"):
        raise HTTPException(422, "不允许删除此缓存键")
    # 不在审计中重复保存原始识别关键词或完整载荷。
    import hashlib

    target = hashlib.sha256(key.encode()).hexdigest()
    # Redis 与 SQL 不能共用事务，先持久化意图，保证删除后 SQL 故障仍有追踪依据。
    record(db, actor, "recognition.delete_requested", target)
    await db.commit()
    if not await get_redis().delete(key):
        raise HTTPException(404, "缓存记录不存在")
    record(db, actor, "recognition.delete", target)
    await db.commit()
