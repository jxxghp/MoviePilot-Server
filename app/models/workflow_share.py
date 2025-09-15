"""
工作流分享模型
"""
from sqlalchemy import Column, Integer, String, or_, and_, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base, get_id_column


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
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}
