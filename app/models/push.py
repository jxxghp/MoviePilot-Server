"""
私有推送的持久化设备绑定；中转密钥与管理令牌仅保存摘要。
"""

from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint

from app.models.base import Base


class PushRelay(Base):
    """
    中心签发的独立实例空间；UID 只关联来源，不能作为访问或恢复凭据。
    """

    __tablename__ = "PUSH_RELAY"

    id = Column(String(32), primary_key=True)
    user_uid = Column(String(128), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)


class PushRelayKey(Base):
    """
    仅保存随机凭据摘要；保留吊销记录，旧密钥不能通过再次注册恢复权限。
    """

    __tablename__ = "PUSH_RELAY_KEY"

    digest = Column(String(64), primary_key=True)
    relay = Column(String(32), nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True)
    replacement = Column(String(64), nullable=False, default="")
    created_at = Column(Integer, nullable=False)


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
