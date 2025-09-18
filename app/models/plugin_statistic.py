"""
插件统计模型
"""
from sqlalchemy import Column, Integer, String, select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base, get_id_column


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

    @classmethod
    async def read_prefer_camel(cls, db: AsyncSession, pid: str):
        """
        根据 plugin_id 进行不区分大小写的查询；如存在两个记录（一个全小写、一个驼峰），优先返回驼峰记录。
        """
        # 获取同一逻辑 plugin_id 的所有记录（不区分大小写）
        result = await db.execute(
            select(cls).where(func.lower(cls.plugin_id) == pid.lower())
        )
        items = result.scalars().all()
        if not items:
            return None
        # 优先返回包含大写字符的记录（视为驼峰写法）
        for item in items:
            if any(ch.isupper() for ch in item.plugin_id):
                return item
        # 如果没有驼峰，返回第一条（应为全小写）
        return items[0]

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
