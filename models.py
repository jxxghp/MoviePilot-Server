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


def list(db: Session, stype: str, page: int = 1, count: int = 30):
    return db.query(SubscribeStatistics).filter(
        or_(SubscribeStatistics.type == stype)
    ).order_by(
        SubscribeStatistics.count.desc()
    ).offset((page - 1) * count).limit(count).all()
