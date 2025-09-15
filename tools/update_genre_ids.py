#!/usr/bin/env python3
"""
批量修复 SUBSCRIBE_STATISTICS 表中缺失的 genre_ids。

逻辑：
- 查询 genre_ids 为空/NULL，且 tmdbid 存在的记录
- 根据记录的 type 决定调用 TMDB 的 movie 或 tv 接口
- 获取 genres 列表并以逗号拼接为字符串，更新回数据库

用法：
  python tools/update_genre_ids.py              # 实际更新
  python tools/update_genre_ids.py --dry-run    # 仅打印将要更新的记录，不落库
  python tools/update_genre_ids.py --limit 500  # 限制本次最多处理 500 条

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
from app.services.tmdb import tmdb_service


async def _fetch_and_update_one(session: AsyncSession, record: SubscribeStatistics, dry_run: bool = False) -> bool:
    """为单条记录获取并更新 genre_ids。返回是否完成更新。"""
    media_type = (record.type or "movie").lower()
    if media_type not in ("movie", "tv"):
        media_type = "movie"

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


async def _run(dry_run: bool, limit: Optional[int]) -> None:
    async with AsyncSessionLocal() as session:
        # 查询条件：genre_ids 为 NULL 或 空字符串；tmdbid 不为 NULL
        query = select(SubscribeStatistics).where(
            and_(
                or_(SubscribeStatistics.genre_ids.is_(None), SubscribeStatistics.genre_ids == ""),
                SubscribeStatistics.tmdbid.isnot(None),
            )
        ).order_by(SubscribeStatistics.id.asc())

        if limit and limit > 0:
            query = query.limit(limit)

        result = await session.execute(query)
        records = list(result.scalars().all())

        if not records:
            print("没有需要修复的记录。")
            return

        print(f"待处理记录数：{len(records)} (dry_run={dry_run})")

        updated = 0
        for record in records:
            if await _fetch_and_update_one(session, record, dry_run=dry_run):
                updated += 1

        # 当 dry_run=False 时，record.update 内部已逐条提交；这里无需再额外提交。
        print(f"完成。成功处理 {updated}/{len(records)} 条。")


def main() -> int:
    parser = argparse.ArgumentParser(description="修复订阅统计表缺失的 genre_ids")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将更新的内容，不写入数据库")
    parser.add_argument("--limit", type=int, default=None, help="最多处理的记录数；默认不限")
    args = parser.parse_args()

    asyncio.run(_run(dry_run=args.dry_run, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
