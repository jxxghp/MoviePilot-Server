"""
订阅统计模型
"""
from typing import Union
from sqlalchemy import Column, Integer, String, Float, or_, and_, select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.base import Base, get_id_column


class SubscribeStatistics(Base):
    """
    订阅统计
    """
    __tablename__ = "SUBSCRIBE_STATISTICS"

    id = get_id_column()
    # 标题
    name = Column(String, nullable=False)
    # 年份
    year = Column(String)
    # 类型
    type = Column(String, index=True)
    # 媒体编号
    tmdbid = Column(Integer, index=True)
    imdbid = Column(String)
    tvdbid = Column(Integer)
    doubanid = Column(String, index=True)
    bangumiid = Column(Integer, index=True)
    # genre_ids,分隔
    genre_ids = Column(String)
    # 季号
    season = Column(Integer)
    # 海报
    poster = Column(String)
    # 背景图
    backdrop = Column(String)
    # 评分，float
    vote = Column(Float)
    # 简介
    description = Column(String)
    # 订阅人次
    count = Column(Integer)

    async def create(self, db: AsyncSession):
        db.add(self)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def read(cls, db: AsyncSession, mid: Union[str, int], season: int):
        # 将 mid 转换为适当的类型进行比较
        if isinstance(mid, str):
            try:
                mid_int = int(mid)
            except ValueError:
                mid_int = None
        else:
            mid_int = mid
            mid = str(mid)

        if season:
            conditions = []
            if mid_int is not None:
                conditions.append(cls.tmdbid == mid_int)
            conditions.append(cls.doubanid == mid)

            result = await db.execute(
                select(cls).where(
                    and_(
                        or_(*conditions),
                        cls.season == season
                    )
                )
            )
        else:
            conditions = []
            if mid_int is not None:
                conditions.append(cls.tmdbid == mid_int)
            conditions.append(cls.doubanid == mid)

            result = await db.execute(
                select(cls).where(
                    or_(*conditions)
                )
            )
        return result.scalars().first()

    async def update(self, db: AsyncSession, payload: dict):
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            if hasattr(self, key):
                setattr(self, key, value)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def delete(cls, db: AsyncSession, sid: int):
        await db.execute(
            delete(cls).where(cls.id == sid)
        )
        await db.commit()

    @classmethod
    async def list(cls, db: AsyncSession, stype: str, page: int = 1, count: int = 30, genre_id: int = None):
        query = select(cls).where(cls.type == stype)
        
        # 如果提供了genre_id，则添加genre_ids过滤条件
        if genre_id is not None:
            query = query.where(cls.genre_ids.like(f'%{genre_id}%'))
        
        result = await db.execute(
            query
            .order_by(cls.count.desc())
            .offset((page - 1) * count)
            .limit(count)
        )
        return result.scalars().all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}