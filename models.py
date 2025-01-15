from typing import Union

from sqlalchemy import Column, Integer, String, Float, or_, and_
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()


class PluginStatistics(Base):
    """
    插件安装统计
    """
    __tablename__ = "PLUGIN_STATISTICS"

    id = Column(Integer, primary_key=True, index=True)
    plugin_id = Column(String, unique=True, index=True)
    count = Column(Integer)

    def create(self, db: Session):
        db.add(self)
        db.commit()
        db.refresh(self)

    @staticmethod
    def read(db: Session, pid: str):
        return db.query(PluginStatistics).filter(or_(PluginStatistics.plugin_id == pid)).first()

    def update(self, db: Session, payload: dict):
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            setattr(self, key, value)
        db.commit()
        db.refresh(self)

    @staticmethod
    def delete(db: Session, pid: int):
        db.query(PluginStatistics).filter(or_(PluginStatistics.plugin_id == pid)).delete()
        db.commit()

    @staticmethod
    def list(db: Session):
        return db.query(PluginStatistics.plugin_id, PluginStatistics.count).all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns} # noqa


class SubscribeStatistics(Base):
    """
    订阅统计
    """
    __tablename__ = "SUBSCRIBE_STATISTICS"

    id = Column(Integer, primary_key=True, index=True)
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

    def create(self, db: Session):
        db.add(self)
        db.commit()
        db.refresh(self)

    @staticmethod
    def read(db: Session, mid: Union[str, int], season: int):
        if season:
            return db.query(SubscribeStatistics).filter(
                and_(
                    or_(
                        SubscribeStatistics.tmdbid == mid,
                        SubscribeStatistics.doubanid == mid
                    ),
                    SubscribeStatistics.season == season
                )
            ).first()
        else:
            return db.query(SubscribeStatistics).filter(or_(SubscribeStatistics.tmdbid == mid,
                                                            SubscribeStatistics.doubanid == mid)).first()

    def update(self, db: Session, payload: dict):
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            if hasattr(self, key):
                setattr(self, key, value)
        db.commit()
        db.refresh(self)

    @staticmethod
    def delete(db: Session, sid: int):
        db.query(SubscribeStatistics).filter(or_(SubscribeStatistics.id == sid)).delete()
        db.commit()

    @staticmethod
    def list(db: Session, stype: str, page: int = 1, count: int = 30):
        return db.query(SubscribeStatistics).filter(
            or_(SubscribeStatistics.type == stype)
        ).order_by(
            SubscribeStatistics.count.desc()
        ).offset((page - 1) * count).limit(count).all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns} # noqa


class SubscribeShare(Base):
    """
    订阅分享
    """
    __tablename__ = "SUBSCRIBE_SHARE"

    id = Column(Integer, primary_key=True, index=True)
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
    # 搜索词
    keyword = Column(String)
    # 自定义识别词
    custom_words = Column(String)
    # 自定义媒体类别
    media_category = Column(String)
    # 创建时间
    date = Column(String, index=True)
    # 复用人次
    count = Column(Integer)

    def create(self, db: Session):
        db.add(self)
        db.commit()
        db.refresh(self)

    @staticmethod
    def read(db: Session, title: str, user: str):
        return db.query(SubscribeShare).filter(and_(SubscribeShare.share_title == title,
                                                    SubscribeShare.share_user == user)).first()

    @staticmethod
    def read_by_id(db: Session, sid: int):
        return db.query(SubscribeShare).filter(and_(SubscribeShare.id == sid)).first()

    def update(self, db: Session, payload: dict):
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            if hasattr(self, key):
                setattr(self, key, value)
        db.commit()
        db.refresh(self)

    @staticmethod
    def delete(db: Session, sid: int):
        db.query(SubscribeShare).filter(or_(SubscribeShare.id == sid)).delete()
        db.commit()

    @staticmethod
    def list(db: Session, name: str, page: int = 1, count: int = 30):
        if name:
            return db.query(SubscribeShare).filter(
                or_(SubscribeShare.share_title.like(f'%{name}%'),
                    SubscribeShare.name.like(f'%{name}%'), )
            ).order_by(
                SubscribeShare.date.desc()
            ).offset((page - 1) * count).limit(count).all()
        else:
            return db.query(SubscribeShare).order_by(
                SubscribeShare.date.desc()
            ).offset((page - 1) * count).limit(count).all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns} # noqa
