"""
插件统计模型
"""
from sqlalchemy import Column, Integer, String, select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base, get_id_column
from app.core.config import settings


class PluginStatistics(Base):
    """
    插件安装统计
    """
    __tablename__ = "PLUGIN_STATISTICS"

    id = get_id_column()
    plugin_id = Column(String, unique=True, index=True)
    count = Column(Integer)
    repo_url = Column(String, nullable=True)

    async def create(self, db: AsyncSession):
        db.add(self)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def read(cls, db: AsyncSession, pid: str):
        result = await db.execute(
            select(cls).where(cls.plugin_id == pid)
        )
        return result.scalar_one_or_none()

    async def update(self, db: AsyncSession, payload: dict):
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            setattr(self, key, value)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def delete(cls, db: AsyncSession, pid: str):
        await db.execute(
            delete(cls).where(cls.plugin_id == pid)
        )
        await db.commit()

    @classmethod
    async def list(cls, db: AsyncSession):
        result = await db.execute(
            select(cls)
        )
        return result.scalars().all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}

    @classmethod
    async def ensure_repo_url_column(cls, db: AsyncSession):
        """Ensure the repo_url column exists (best-effort runtime migration)."""
        try:
            if settings.is_postgresql:
                await db.execute(text('ALTER TABLE "PLUGIN_STATISTICS" ADD COLUMN IF NOT EXISTS repo_url VARCHAR'))
                await db.commit()
                return
            # SQLite path
            result = await db.execute(text("PRAGMA table_info('PLUGIN_STATISTICS')"))
            columns = [row[1] for row in result.fetchall()]
            if 'repo_url' not in columns:
                await db.execute(text("ALTER TABLE PLUGIN_STATISTICS ADD COLUMN repo_url TEXT"))
                await db.commit()
        except Exception:
            # Ignore migration errors to avoid breaking runtime
            await db.rollback()
