"""
安装版本统计模型
"""
from sqlalchemy import Column, Integer, String, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base, get_id_column


class UsageStatistics(Base):
    """
    安装版本统计
    """
    __tablename__ = "USAGE_STATISTICS"

    id = get_id_column()
    user_uid = Column(String, unique=True, index=True, nullable=False)
    backend_version = Column(String, nullable=True, index=True)
    frontend_version = Column(String, nullable=True, index=True)
    version_flag = Column(String, nullable=True, index=True)
    platform = Column(String, nullable=True)
    arch = Column(String, nullable=True)
    first_seen_at = Column(String, nullable=True, index=True)
    last_seen_at = Column(String, nullable=True, index=True)
    report_count = Column(Integer, nullable=False, default=1)

    @classmethod
    async def upsert_by_user_uid(
            cls,
            db: AsyncSession,
            user_uid: str,
            payload: dict,
            now: str,
    ) -> int:
        """
        按用户唯一ID更新安装版本统计，返回更新行数。
        """
        result = await db.execute(
            update(cls)
            .where(cls.user_uid == user_uid)
            .values(
                **payload,
                last_seen_at=now,
                report_count=func.coalesce(cls.report_count, 0) + 1,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0

    @classmethod
    async def count_all(cls, db: AsyncSession) -> int:
        """
        统计总安装用户数。
        """
        result = await db.execute(select(func.count(cls.id)))
        return result.scalar_one() or 0

    @classmethod
    async def count_active_since(cls, db: AsyncSession, since: str) -> int:
        """
        统计指定时间后的活跃安装用户数。
        """
        result = await db.execute(
            select(func.count(cls.id)).where(cls.last_seen_at >= since)
        )
        return result.scalar_one() or 0

    @classmethod
    async def list_backend_version_counts(cls, db: AsyncSession):
        """
        按后端版本统计安装用户数。
        """
        count_expr = func.count(cls.id)
        result = await db.execute(
            select(cls.backend_version, count_expr)
            .group_by(cls.backend_version)
            .order_by(count_expr.desc())
        )
        return result.all()

    @classmethod
    async def list_frontend_version_counts(cls, db: AsyncSession):
        """
        按前端版本统计安装用户数。
        """
        count_expr = func.count(cls.id)
        result = await db.execute(
            select(cls.frontend_version, count_expr)
            .group_by(cls.frontend_version)
            .order_by(count_expr.desc())
        )
        return result.all()
