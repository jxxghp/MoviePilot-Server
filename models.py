import os
from typing import Union

from sqlalchemy import Column, Integer, String, Float, or_, and_, func, select, delete, Identity, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base

Base = declarative_base()


DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'sqlite').lower()

def get_id_column():
    """
    根据数据库类型返回合适的ID列定义
    """
    if DATABASE_TYPE == "postgresql":
        # PostgreSQL使用SERIAL类型，让数据库自动处理序列
        return Column(Integer, Identity(start=1, cycle=True), primary_key=True, index=True)
    else:
        # SQLite使用Sequence
        return Column(Integer, Sequence('id'), primary_key=True, index=True)


class PluginStatistics(Base):
    """
    插件安装统计
    """
    __tablename__ = "PLUGIN_STATISTICS"

    id = get_id_column()
    plugin_id = Column(String, unique=True, index=True)
    count = Column(Integer)

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
            select(cls.plugin_id, cls.count)
        )
        return result.all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}  # noqa


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
    async def list(cls, db: AsyncSession, stype: str, page: int = 1, count: int = 30):
        result = await db.execute(
            select(cls)
            .where(cls.type == stype)
            .order_by(cls.count.desc())
            .offset((page - 1) * count)
            .limit(count)
        )
        return result.scalars().all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}  # noqa


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
    async def list(cls, db: AsyncSession, name: str, page: int = 1, count: int = 30):
        if name:
            result = await db.execute(
                select(cls)
                .where(
                    or_(
                        cls.share_title.like(f'%{name}%'),
                        cls.name.like(f'%{name}%')
                    )
                )
                .order_by(cls.date.desc())
                .offset((page - 1) * count)
                .limit(count)
            )
        else:
            result = await db.execute(
                select(cls)
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
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}  # noqa


class WorkflowShare(Base):
    """
    工作流分享
    """
    __tablename__ = "WORKFLOW_SHARE"

    id = get_id_column()
    # 分享标题
    share_title = Column(String, index=True, nullable=False)
    # 分享介绍
    share_comment = Column(String)
    # 分享人
    share_user = Column(String)
    # 分享人唯一ID
    share_uid = Column(String)
    # 工作流名称
    name = Column(String, index=True, nullable=False)
    # 工作流描述
    description = Column(String)
    # 定时器
    timer = Column(String)
    # 任务列表
    actions = Column(String)  # JSON字符串
    # 任务流
    flows = Column(String)  # JSON字符串
    # 执行上下文
    context = Column(String)  # JSON字符串
    # 创建时间
    date = Column(String, index=True)
    # 复用人次
    count = Column(Integer, default=0)

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
    async def list(cls, db: AsyncSession, name: str, page: int = 1, count: int = 30):
        if name:
            result = await db.execute(
                select(cls)
                .where(
                    or_(
                        cls.share_title.like(f'%{name}%'),
                        cls.name.like(f'%{name}%')
                    )
                )
                .order_by(cls.date.desc())
                .offset((page - 1) * count)
                .limit(count)
            )
        else:
            result = await db.execute(
                select(cls)
                .order_by(cls.date.desc())
                .offset((page - 1) * count)
                .limit(count)
            )
        return result.scalars().all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}  # noqa
