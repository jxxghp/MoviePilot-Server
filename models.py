from sqlalchemy import Column, Integer, String, func
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()


class PluginStatistics(Base):
    __tablename__ = "PLUGIN_STATISTICS"

    id = Column(Integer, primary_key=True, index=True)
    plugin_id = Column(String, unique=True, index=True)
    count = Column(Integer)

    def create(self, db: Session):
        """
        创建插件统计
        :param db:
        :return:
        """
        db.add(self)
        db.commit()
        db.refresh(self)

    @staticmethod
    def read(db: Session, pid: str):
        """
        读取单个插件统计
        :param db:
        :param pid:
        :return:
        """
        return db.query(PluginStatistics).filter(PluginStatistics.plugin_id == pid).first()

    def update(self, db: Session, payload: dict):
        """
        更新插件统计
        :param db:
        :param payload:
        :return:
        """
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            setattr(self, key, value)
        db.commit()
        db.refresh(self)

    @staticmethod
    def delete(db: Session, pid: int):
        """
        删除单个插件统计
        :param db:
        :param pid:
        :return:
        """
        db.query(PluginStatistics).filter(PluginStatistics.plugin_id == pid).delete()
        db.commit()

    @staticmethod
    def list(db: Session):
        """
        统计所有插件安装数量
        :param db:
        :return:
        """
        return db.query(PluginStatistics.plugin_id, PluginStatistics.count).all()
