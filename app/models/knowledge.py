"""
中心知识的集合、站点、来源、文档修订与独立访问授权。

本阶段只保存受控管理数据，不向公共接口发布正文或启动采集。
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.base import Base


def new_identity() -> str:
    """
    为知识实体生成不依赖数据库序列的稳定标识。
    """
    return uuid4().hex


def timestamp() -> str:
    """
    保存统一的带时区 UTC 时间，不把录入时间当作来源采集时间。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KnowledgeRecord:
    """
    知识管理实体共用的身份、并发版本和维护时间。

    :cvar id: 服务器生成的稳定标识
    :cvar version: 乐观并发版本，每次成功修改递增
    :cvar created_at: 首次创建时间
    :cvar updated_at: 最近管理修改时间
    """

    id = Column(String(32), primary_key=True, default=new_identity)
    version = Column(Integer, nullable=False, default=1, comment="乐观并发版本")
    created_at = Column(String(40), nullable=False, default=timestamp, comment="创建时间")
    updated_at = Column(String(40), nullable=False, default=timestamp, comment="最近修改时间")


class KnowledgeCollection(KnowledgeRecord, Base):
    """
    管理和授权知识的稳定集合；名称不限制为内置分类。
    """

    __tablename__ = "KNOWLEDGE_COLLECTION"
    __table_args__ = (
        CheckConstraint("domain IN ('mp', 'pt') AND version > 0", name="ck_knowledge_collection_domain"),
    )

    slug = Column(String(64), nullable=False, unique=True, comment="不可变集合标识")
    name = Column(String(120), nullable=False, comment="集合显示名称")
    description = Column(Text, nullable=False, default="", comment="集合用途")
    domain = Column(String(8), nullable=False, comment="MP 或 PT 知识领域")
    product = Column(String(64), nullable=False, default="", comment="对应产品，空值表示通用知识")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否允许后续授权与检索")


class KnowledgeSite(KnowledgeRecord, Base):
    """
    独立维护站点身份；名称、域名与别名通过名称表统一排除歧义。
    """

    __tablename__ = "KNOWLEDGE_SITE"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_knowledge_site_version"),
    )

    name = Column(String(120), nullable=False, comment="站点显示名称")
    domain = Column(String(253), nullable=False, unique=True, comment="规范化主域名")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用本站知识")


class KnowledgeSiteName(Base):
    """
    站点名称与别名的全局唯一索引，禁止同一称呼指向两个站点。
    """

    __tablename__ = "KNOWLEDGE_SITE_NAME"

    normalized = Column(String(253), primary_key=True, comment="规范化名称或域名")
    site_id = Column(String(32), ForeignKey("KNOWLEDGE_SITE.id"), nullable=False, index=True, comment="所属站点")
    label = Column(String(253), nullable=False, comment="保留显示形式的名称")
    kind = Column(String(16), nullable=False, comment="名称、域名或人工别名")


class KnowledgeSource(KnowledgeRecord, Base):
    """
    后台维护的来源配置；本表只允许非敏感定位与解析参数。
    """

    __tablename__ = "KNOWLEDGE_SOURCE"
    __table_args__ = (
        CheckConstraint("connector IN ('manual', 'jsonl', 'github', 'http', 'nexusphp')", name="ck_knowledge_source_connector"),
        CheckConstraint("refresh_minutes >= 0 AND version > 0", name="ck_knowledge_source_refresh"),
        Index("ix_knowledge_source_collection", "collection_id", "enabled"),
    )

    collection_id = Column(String(32), ForeignKey("KNOWLEDGE_COLLECTION.id"), nullable=False, comment="所属集合")
    site_id = Column(String(32), ForeignKey("KNOWLEDGE_SITE.id"), nullable=True, index=True, comment="可选站点")
    name = Column(String(120), nullable=False, comment="来源显示名称")
    connector = Column(String(16), nullable=False, comment="受支持的采集连接器")
    url = Column(String(2048), nullable=False, default="", comment="不含凭据的 HTTPS 来源地址")
    repository = Column(String(160), nullable=False, default="", comment="GitHub 仓库所有者与名称")
    branch = Column(String(128), nullable=False, default="main", comment="指定仓库分支")
    paths = Column(Text, nullable=False, default="[]", comment="受限页面或仓库路径列表")
    selector = Column(String(200), nullable=False, default="", comment="正文选择器，不执行脚本")
    refresh_minutes = Column(Integer, nullable=False, default=0, comment="刷新间隔分钟，零表示仅手动")
    enabled = Column(Boolean, nullable=False, default=False, comment="是否允许后续采集")


class KnowledgeDocument(KnowledgeRecord, Base):
    """
    绑定来源和外部身份的文档；修订正文单独追加，不能原地覆盖。
    """

    __tablename__ = "KNOWLEDGE_DOCUMENT"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_knowledge_document_identity"),
        CheckConstraint("head_revision >= 1 AND version > 0", name="ck_knowledge_document_revision"),
    )

    source_id = Column(String(32), ForeignKey("KNOWLEDGE_SOURCE.id"), nullable=False, index=True, comment="固定来源")
    external_id = Column(String(512), nullable=False, comment="来源内稳定身份")
    head_revision = Column(Integer, nullable=False, default=1, comment="最新修订号")
    published_revision = Column(Integer, nullable=True, comment="已发布修订号，尚未发布为 null")


class KnowledgeRevision(Base):
    """
    不可变的手工或采集修订，保留未知的版本和采集时间。
    """

    __tablename__ = "KNOWLEDGE_REVISION"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_knowledge_revision_number"),
        CheckConstraint("number > 0", name="ck_knowledge_revision_number"),
        CheckConstraint("version_scope IN ('unknown', 'all', 'range')", name="ck_knowledge_revision_scope"),
        CheckConstraint("state IN ('draft', 'review', 'published', 'superseded', 'withdrawn')", name="ck_knowledge_revision_state"),
    )

    id = Column(String(32), primary_key=True, default=new_identity)
    document_id = Column(String(32), ForeignKey("KNOWLEDGE_DOCUMENT.id"), nullable=False, index=True, comment="所属文档")
    number = Column(Integer, nullable=False, comment="文档内修订号")
    title = Column(String(300), nullable=False, comment="文档标题")
    content = Column(Text, nullable=False, comment="私有知识正文")
    content_hash = Column(String(64), nullable=False, comment="正文完整性摘要")
    product = Column(String(64), nullable=False, default="", comment="适用产品")
    version_scope = Column(String(16), nullable=False, default="unknown", comment="未知、通用或明确版本范围")
    min_version = Column(String(32), nullable=True, comment="稳定版本范围下界，含边界")
    max_version = Column(String(32), nullable=True, comment="稳定版本范围上界，含边界")
    source_url = Column(String(2048), nullable=False, default="", comment="不含凭据的出处")
    source_commit = Column(String(64), nullable=False, default="", comment="来源明确提供的源码快照")
    fetched_at = Column(String(40), nullable=True, comment="已知的实际采集时间")
    valid_until = Column(String(40), nullable=True, comment="有效期截止时间")
    state = Column(String(16), nullable=False, default="draft", comment="发布生命周期状态")
    created_at = Column(String(40), nullable=False, default=timestamp, comment="中心录入时间")
    created_by = Column(String(64), nullable=False, comment="维护者或后续采集任务身份")


class KnowledgeClient(KnowledgeRecord, Base):
    """
    中心签发的独立知识客户端；仅存随机令牌摘要，不复用推送或后台会话。
    """

    __tablename__ = "KNOWLEDGE_CLIENT"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_knowledge_client_version"),
    )

    name = Column(String(120), nullable=False, comment="管理员维护的客户端标签，不作为身份凭证")
    token_hash = Column(String(64), nullable=False, unique=True, comment="当前随机令牌的 SHA-256 摘要")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否允许使用知识服务")
    expires_at = Column(String(40), nullable=True, comment="带时区的令牌截止时间，空值表示不设期限")


class KnowledgeGrant(Base):
    """
    超级管理员明确分配的集合读取范围；没有记录不代表可以读取全部集合。
    """

    __tablename__ = "KNOWLEDGE_GRANT"

    client_id = Column(String(32), ForeignKey("KNOWLEDGE_CLIENT.id"), primary_key=True, comment="已签发客户端")
    collection_id = Column(String(32), ForeignKey("KNOWLEDGE_COLLECTION.id"), primary_key=True,
                           index=True, comment="被授权读取的集合")


class KnowledgeState(Base):
    """
    SQL 中的知识事实代次与已激活私有索引；代次用于阻止陈旧构建覆盖当前状态。
    """

    __tablename__ = "KNOWLEDGE_STATE"
    __table_args__ = (
        CheckConstraint("generation >= active_generation AND active_generation >= 0", name="ck_knowledge_state_generation"),
    )

    id = Column(String(16), primary_key=True, comment="内部固定命名空间")
    generation = Column(Integer, nullable=False, default=0, comment="发布、撤回或内容开关改变的代次")
    active_generation = Column(Integer, nullable=False, default=0, comment="当前快照对应代次")
    active_snapshot = Column(String(32), nullable=False, default="", comment="中心私有快照标识，不存可控路径")


class KnowledgeJob(KnowledgeRecord, Base):
    """
    可跨进程恢复的知识任务；令牌限定单次租约，错误只保存白名单代码。
    """

    __tablename__ = "KNOWLEDGE_JOB"
    __table_args__ = (
        CheckConstraint("kind IN ('index', 'import', 'collect')", name="ck_knowledge_job_kind"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
                        name="ck_knowledge_job_status"),
        CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 5 AND version > 0", name="ck_knowledge_job_attempts"),
        Index("ix_knowledge_job_queue", "status", "available_at"),
    )

    kind = Column(String(16), nullable=False, comment="固定任务类型，不接收函数名或可执行脚本")
    source_id = Column(String(32), ForeignKey("KNOWLEDGE_SOURCE.id"), nullable=True, comment="采集或导入的固定来源")
    status = Column(String(16), nullable=False, default="queued", comment="真实持久化执行状态")
    attempts = Column(Integer, nullable=False, default=0, comment="当前任务已认领次数")
    max_attempts = Column(Integer, nullable=False, default=3, comment="失败重试上限")
    available_at = Column(Float, nullable=False, comment="允许认领的 UTC Unix 时间")
    lease_token = Column(String(32), nullable=False, default="", comment="本次认领的内部随机所有权标记")
    lease_until = Column(Float, nullable=True, comment="租约到期的 UTC Unix 时间")
    cancel_requested = Column(Boolean, nullable=False, default=False, comment="已请求取消，执行器须在激活前复核")
    processed_documents = Column(Integer, nullable=False, default=0, comment="已构建文档数")
    processed_chunks = Column(Integer, nullable=False, default=0, comment="已构建片段数")
    outcome = Column(String(32), nullable=False, default="", comment="固定完成结果或错误码")
    started_at = Column(String(40), nullable=True, comment="本次开始时间")
    finished_at = Column(String(40), nullable=True, comment="终态时间")
    created_by = Column(String(64), nullable=False, comment="触发任务的管理者")


class KnowledgeJobSlot(Base):
    """
    同一索引或来源的活动任务去重槽，结束后仅释放属于自己的槽位。
    """

    __tablename__ = "KNOWLEDGE_JOB_SLOT"

    key = Column(String(64), primary_key=True, comment="内部计算的去重键")
    job_id = Column(String(32), ForeignKey("KNOWLEDGE_JOB.id"), nullable=False, unique=True, comment="当前活动任务")
