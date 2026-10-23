"""
私有推送的持久化设备绑定；中转密钥与管理令牌仅保存摘要。
"""

from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.models.base import Base


class PushDevice(Base):
    """
    保存实例命名空间、用户与安装之间的绑定，服务重启后仍可投递。
    """

    __tablename__ = "PUSH_DEVICE"
    __table_args__ = (UniqueConstraint("namespace", "installation", name="uq_push_installation"),
                      UniqueConstraint("transport", "fingerprint", name="uq_push_destination"))

    id = Column(String(64), primary_key=True)
    namespace = Column(String(64), nullable=False, index=True)
    owner = Column(String(64), nullable=False, index=True)
    installation = Column(String(64), nullable=False)
    parent_installation = Column(String(64), nullable=False, default="")
    transport = Column(String(16), nullable=False)
    destination = Column(Text, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    origin = Column(String(2048), nullable=False)
    topic = Column(String(255), nullable=False, default="")
    environment = Column(String(16), nullable=False, default="")
    secret_hash = Column(String(64), nullable=False, default="")
    revision = Column(String(32), nullable=False)
    updated_at = Column(Integer, nullable=False)
