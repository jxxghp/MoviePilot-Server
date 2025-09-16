#!/usr/bin/env python3
"""
批量修复 SUBSCRIBE_STATISTICS 与 SUBSCRIBE_SHARE 表中缺失的 genre_ids。

逻辑：
- 查询两张表中 genre_ids 为空/NULL，且 tmdbid 存在的记录
- 根据记录的 type 决定调用 TMDB 的 movie 或 tv 接口
  - 修正：支持中文类型值（"电影"-> movie，"电视剧"-> tv）
- 获取 genres 列表并以逗号拼接为字符串，更新回数据库

用法：
  python tools/update_genre_ids.py              # 实际更新
  python tools/update_genre_ids.py --dry-run    # 仅打印将要更新的记录，不落库
  python tools/update_genre_ids.py --limit 500  # 限制本次每张表最多处理 500 条

依赖：
- 读取现有配置 `app.core.config.settings`
- 复用 `app.db.database.AsyncSessionLocal`
- 复用 `app.services.tmdb.tmdb_service`
"""

import argparse
import asyncio
from typing import Optional

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.models.subscribe_statistics import SubscribeStatistics
from app.models.subscribe_share import SubscribeShare
from app.services.tmdb import tmdb_service


def _normalize_media_type(raw_type: Optional[str]) -> str:
    """将表中 `type` 规范化为 TMDB 可识别的类型: movie/tv。默认 movie。"""
    if not raw_type:
        return "movie"
    value = str(raw_type).strip().lower()
    # 英文直接映射
    if value in ("movie", "tv"):
        return value
    # 中文到英文的映射
    # 电影 -> movie；电视剧 -> tv
    if value in ("电影",):
        return "movie"
    if value in ("电视剧",):
        return "tv"
    # 兜底
    return "movie"


async def _fetch_and_update_stat(session: AsyncSession, record: SubscribeStatistics, dry_run: bool = False) -> bool:
    """为订阅统计表的单条记录获取并更新 genre_ids。返回是否完成更新。"""
    media_type = _normalize_media_type(record.type)

    if not record.tmdbid:
        return False

    try:
        genre_ids: Optional[str] = await tmdb_service.get_genre_ids(record.tmdbid, media_type)
    except Exception as exc:  # 网络等异常
        print(f"[SKIP] tmdbid={record.tmdbid} 获取失败: {exc}")
        return False

    if not genre_ids:
        print(f"[MISS] 无法获取 genres: id={record.id} tmdbid={record.tmdbid} type={media_type}")
        return False

    if dry_run:
        print(f"[DRY] 将更新 id={record.id} tmdbid={record.tmdbid} type={media_type} -> genre_ids='{genre_ids}'")
        return True

    # 使用模型自带的 update 以保持一致的提交和刷新逻辑
    await record.update(session, {"genre_ids": genre_ids})
    print(f"[OK] 已更新 id={record.id} tmdbid={record.tmdbid} -> genre_ids='{genre_ids}'")
    return True


async def _fetch_and_update_share(session: AsyncSession, record: SubscribeShare, dry_run: bool = False) -> bool:
    """为订阅分享表的单条记录获取并更新 genre_ids。返回是否完成更新。"""
    media_type = _normalize_media_type(record.type)

    if not record.tmdbid:
        return False

    try:
        genre_ids: Optional[str] = await tmdb_service.get_genre_ids(record.tmdbid, media_type)
    except Exception as exc:
        print(f"[SKIP] share tmdbid={record.tmdbid} 获取失败: {exc}")
        return False

    if not genre_ids:
        print(f"[MISS] 无法获取 genres: share id={record.id} tmdbid={record.tmdbid} type={media_type}")
        return False

    if dry_run:
        print(f"[DRY] 将更新 share id={record.id} tmdbid={record.tmdbid} type={media_type} -> genre_ids='{genre_ids}'")
        return True

    await record.update(session, {"genre_ids": genre_ids})
    print(f"[OK] 已更新 share id={record.id} tmdbid={record.tmdbid} -> genre_ids='{genre_ids}'")
    return True


async def _run(dry_run: bool, limit: Optional[int]) -> None:
    async with AsyncSessionLocal() as session:
        # 1) 订阅统计表
        stat_query = select(SubscribeStatistics).where(
            and_(
                or_(SubscribeStatistics.genre_ids.is_(None), SubscribeStatistics.genre_ids == ""),
                SubscribeStatistics.tmdbid.isnot(None),
            )
        ).order_by(SubscribeStatistics.id.asc())

        if limit and limit > 0:
            stat_query = stat_query.limit(limit)

        stat_result = await session.execute(stat_query)
        stat_records = list(stat_result.scalars().all())

        print(f"[STAT] 待处理记录数：{len(stat_records)} (dry_run={dry_run})")
        stat_updated = 0
        for record in stat_records:
            if await _fetch_and_update_stat(session, record, dry_run=dry_run):
                stat_updated += 1

        print(f"[STAT] 完成。成功处理 {stat_updated}/{len(stat_records)} 条。")

        # 2) 订阅分享表
        share_query = select(SubscribeShare).where(
            and_(
                or_(SubscribeShare.genre_ids.is_(None), SubscribeShare.genre_ids == ""),
                SubscribeShare.tmdbid.isnot(None),
            )
        ).order_by(SubscribeShare.id.asc())

        if limit and limit > 0:
            share_query = share_query.limit(limit)

        share_result = await session.execute(share_query)
        share_records = list(share_result.scalars().all())

        print(f"[SHARE] 待处理记录数：{len(share_records)} (dry_run={dry_run})")
        share_updated = 0
        for record in share_records:
            if await _fetch_and_update_share(session, record, dry_run=dry_run):
                share_updated += 1

        print(f"[SHARE] 完成。成功处理 {share_updated}/{len(share_records)} 条。")


def main() -> int:
    parser = argparse.ArgumentParser(description="修复订阅统计与订阅分享缺失的 genre_ids")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将更新的内容，不写入数据库")
    parser.add_argument("--limit", type=int, default=None, help="每张表最多处理的记录数；默认不限")
    args = parser.parse_args()

    asyncio.run(_run(dry_run=args.dry_run, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
