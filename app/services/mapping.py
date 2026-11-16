"""
共享媒体对应关系的持久化和无歧义查询。
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mapping import MediaMapping as MappingRow
from app.schemas.mapping import MediaMapping, MediaMappingQuery


class MediaMappingService:
    """
    公共写入只补缺，后台纠正和撤销拥有最终决定权。
    """

    @staticmethod
    def payload(row: MappingRow) -> dict:
        """
        隔离数据库内部字段和未指定季号哨兵。
        """
        return MediaMapping(
            media_source=row.media_source, media_id=row.media_id, type=row.type,
            season=row.season if row.season >= 0 else None,
            tmdb_id=row.tmdb_id, tmdb_type=row.tmdb_type, tmdb_season=row.tmdb_season,
        ).model_dump(mode="json")

    @classmethod
    async def query(cls, db: AsyncSession, query: MediaMappingQuery) -> dict | None:
        """
        精确季优先；类型或季号未知时，多个不同目标一律不猜测。
        """
        statement = select(MappingRow).where(
            MappingRow.media_source == query.media_source.value,
            MappingRow.media_id == query.media_id,
        )
        if query.type:
            statement = statement.where(MappingRow.type == query.type)
        if query.season is None:
            # 只读取至多两个不同目标，避免整剧身份积累多季后逐行扫描到应用内存。
            variants = (await db.execute(statement.with_only_columns(
                MappingRow.type, MappingRow.tmdb_type, MappingRow.tmdb_id,
                MappingRow.tmdb_season, MappingRow.blocked,
            ).distinct().limit(2))).all()
            if len(variants) != 1 or variants[0].blocked:
                return None
            target = variants[0]
            return MediaMapping(
                media_source=query.media_source, media_id=query.media_id, type=target.type,
                season=None, tmdb_id=target.tmdb_id, tmdb_type=target.tmdb_type, tmdb_season=target.tmdb_season,
            ).model_dump(mode="json")
        if query.season is not None:
            statement = statement.where(MappingRow.season.in_((-1, query.season)))
        # 唯一约束保证每种类型最多两行（精确季与未指定季）。
        rows = list((await db.scalars(statement.limit(4))).all())
        if query.season is not None:
            exact = [row for row in rows if row.season == query.season]
            rows = exact or [row for row in rows if row.season == -1]
        if not rows or any(row.blocked for row in rows):
            return None
        targets = {(row.tmdb_type, row.tmdb_id, row.tmdb_season) for row in rows}
        return cls.payload(rows[0]) if len(targets) == 1 else None

    @classmethod
    async def share(cls, db: AsyncSession, item: MediaMapping) -> dict | None:
        """
        原子插入缺失关系；并发、重复或冲突上报均不覆写已有记录。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = item.model_dump(mode="json")
        values.update(season=item.season if item.season is not None else -1, blocked=False,
                      created_at=now, updated_at=now)
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        await db.execute(insert(MappingRow).values(**values).on_conflict_do_nothing(
            index_elements=["media_source", "media_id", "type", "season"],
        ))
        await db.commit()
        return await cls.query(db, MediaMappingQuery(**item.model_dump(include={"media_source", "media_id", "type", "season"})))
