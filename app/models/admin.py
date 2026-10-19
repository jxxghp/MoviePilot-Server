"""后台独立账号、运行配置和审计记录的持久化模型。"""

from sqlalchemy import Boolean, Column, Integer, String, Text

from app.models.base import Base, get_id_column


class AdminUser(Base):
    """仅用于 Server 后台登录的账号，与实例 UID 和 GitHub 权限名单分离。"""

    __tablename__ = "ADMIN_USER"
    id = get_id_column()
    username = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(80), nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(20), nullable=False, default="admin")
    enabled = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=True)
    auth_version = Column(Integer, nullable=False, default=1)
    created_at = Column(String, nullable=False)
    last_login_at = Column(String, nullable=True)


class AdminSetting(Base):
    """跨 worker 生效的后台配置；缺失配置项时沿用部署环境变量。"""

    __tablename__ = "ADMIN_SETTING"
    key = Column(String(80), primary_key=True)
    value = Column(Text, nullable=False)


class AdminAudit(Base):
    """追加式操作记录，不保存密码、会话或完整业务载荷。"""

    __tablename__ = "ADMIN_AUDIT"
    id = get_id_column()
    actor = Column(String(64), nullable=False, index=True)
    action = Column(String(80), nullable=False, index=True)
    target = Column(String(200), nullable=False)
    detail = Column(Text, nullable=False, default="{}")
    created_at = Column(String, nullable=False, index=True)
