"""
订阅分享模型
"""
from sqlalchemy import Column, Integer, String, Float, or_, and_, func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.base import Base, get_id_column


class SubscribeShare(Base):
    """
    订阅分享
    """
    __tablename__ = "SUBSCRIBE_SHARE"

    id = get_id_column()
    # 分享标题
    share_title = Column(String, index=True, nullable=False)
    # 分享介绍
    share_comment = Column(String)
    # 分享人
    share_user = Column(String)
    # 分享人唯一ID
    share_uid = Column(String)
    # 媒体名称
    name = Column(String, index=True, nullable=False)
    # 搜索关键词
    keyword = Column(String)
    # 年份
    year = Column(String)
    # 类型
    type = Column(String)
    # 媒体编号
    tmdbid = Column(Integer)
    imdbid = Column(String)
    tvdbid = Column(Integer)
    doubanid = Column(String)
    bangumiid = Column(Integer)
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
    # 包含
    include = Column(String)
    # 排除
    exclude = Column(String)
    # 质量
    quality = Column(String)
    # 分辨率
    resolution = Column(String)
    # 特效
    effect = Column(String)
    # 总集数
    total_episode = Column(Integer)
    # 自定义识别词
    custom_words = Column(String)
    # 自定义媒体类别
    media_category = Column(String)
    # 自定义剧集组
    episode_group = Column(String)
    # 创建时间
    date = Column(String, index=True)
    # 复用人次
    count = Column(Integer)

    async def create(self, db: AsyncSession):
        db.add(self)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def read(cls, db: AsyncSession, title: str, user: str):
        result = await db.execute(
            select(cls).where(
                and_(
                    cls.share_title == title,
                    cls.share_user == user
                )
            )
        )
        return result.scalars().first()

    @classmethod
    async def read_by_id(cls, db: AsyncSession, sid: int):
        result = await db.execute(
            select(cls).where(cls.id == sid)
        )
        return result.scalar_one_or_none()

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
    async def list(cls, db: AsyncSession, name: str, page: int = 1, count: int = 30, genre_id: int = None, min_rating: float = None, max_rating: float = None):
        if name:
            query = select(cls).where(
                or_(
                    cls.share_title.like(f'%{name}%'),
                    cls.name.like(f'%{name}%')
                )
            )
        else:
            query = select(cls)
        
        # 如果提供了genre_id，则添加genre_ids过滤条件
        if genre_id is not None:
            query = query.where(cls.genre_ids.like(f'%{genre_id}%'))
        
        # 如果提供了评分范围，则添加评分过滤条件
        if min_rating is not None:
            query = query.where(cls.vote >= min_rating)
        if max_rating is not None:
            query = query.where(cls.vote <= max_rating)
        
        result = await db.execute(
            query
            .order_by(cls.date.desc())
            .offset((page - 1) * count)
            .limit(count)
        )
        return result.scalars().all()

    @classmethod
    async def share_statistics(cls, db: AsyncSession):
        """
        统计每个分享人分享的媒体数量以及总的复用人次
        """
        result = await db.execute(
            select(
                cls.share_user,
                func.count(cls.id).label('share_count'),
                func.sum(cls.count).label('total_reuse_count')
            )
            .group_by(cls.share_user)
            .order_by(func.count(cls.id).desc())
        )
        return result.all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}