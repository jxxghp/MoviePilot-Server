"""
知识后台维护请求的白名单与规范化，禁止把秘密混入来源参数。
"""

import ipaddress
import re
import unicodedata
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


def normalized_name(value: str) -> str:
    """
    统一全半角、大小写与空白，用于检测站点别名碰撞。
    """
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def public_url(value: str) -> str:
    """
    校验不含秘密的公开 HTTPS 定位地址；实际采集仍须校验 DNS 与重定向。
    """
    if not value:
        return ""
    if any(ord(char) < 33 or char == "\\" for char in value):
        raise ValueError("来源地址不能包含空白、控制字符或反斜线")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode().lower().rstrip(".")
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise ValueError("来源地址格式无效") from error
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("来源地址必须为不含凭据和片段的 HTTPS 地址")
    if port not in (None, 443):
        raise ValueError("来源地址只允许标准 HTTPS 端口")
    if host == "localhost" or "." not in host or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("来源地址不能指向本地网络")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("来源地址不能指向非公开网络")
    sensitive = {"cookie", "passkey", "token", "access_token", "auth", "authkey", "api_key", "api-key", "apikey",
                 "key", "password", "passwd", "pwd", "authorization", "secret", "secretkey", "csrf_token"}
    if any(normalized_name(key) in sensitive for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError("来源地址不能包含认证参数")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def stable_version(value: str | None) -> str | None:
    """
    规范明确填写的稳定版本；不将未知、分支名或开发版猜成稳定版本。
    """
    if value is None:
        return None
    match = re.fullmatch(r"v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?", value)
    if not match:
        raise ValueError("版本边界需为明确的稳定版本，例如 3.2.7")
    return ".".join(str(int(part or 0)) for part in match.groups())


class KnowledgePayload(BaseModel):
    """
    知识管理请求只接受显式字段，不静默接收角色、凭据或发布状态。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CollectionPayload(KnowledgePayload):
    """
    创建知识集合，集合标识创建后保持稳定。
    """

    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$", description="稳定集合标识")
    name: str = Field(min_length=1, max_length=120, description="显示名称")
    description: str = Field(default="", max_length=2000, description="集合用途")
    domain: Literal["mp", "pt"] = Field(description="知识领域")
    product: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9-]{0,63})?$", description="产品标识，通用知识可留空")
    enabled: bool = Field(default=True, description="是否允许后续授权使用")


class CollectionUpdate(CollectionPayload):
    """
    使用完整表单和当前版本更新集合；不允许改成另一个稳定标识。
    """

    expected_version: int = Field(ge=1, description="读取表单时的并发版本")


class ClientPayload(KnowledgePayload):
    """
    超级管理员明确维护客户端状态及集合范围，不接收自报身份或明文令牌。
    """

    name: str = Field(min_length=1, max_length=120, description="客户端显示标签")
    enabled: bool = Field(default=True, description="是否允许使用知识服务")
    expires_at: datetime | None = Field(default=None, description="带时区的截止时间，null 为不设期限")
    collection_ids: list[Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]] = Field(
        default_factory=list, max_length=100, description="明确授权的集合 ID，空列表不授予任何读取权限",
    )

    @field_validator("expires_at")
    @classmethod
    def expiry_timezone(cls, value: datetime | None) -> datetime | None:
        """
        令牌到期使用统一 UTC，拒绝依赖服务器本地时区猜测。
        """
        if value is not None and value.utcoffset() is None:
            raise ValueError("截止时间必须包含时区")
        return value.astimezone(timezone.utc) if value is not None else None

    @field_validator("collection_ids")
    @classmethod
    def distinct_collections(cls, values: list[str]) -> list[str]:
        """
        显式拒绝重复授权，避免列表数量与实际权限不一致。
        """
        if len(values) != len(set(values)):
            raise ValueError("不能重复填写同一集合")
        return sorted(values)


class ClientUpdate(ClientPayload):
    """
    按读取版本替换客户端状态和完整授权列表，不能覆盖并发轮换结果。
    """

    expected_version: int = Field(ge=1, description="读取客户端时的并发版本")


class CredentialRotation(KnowledgePayload):
    """
    轮换只接受当前版本，新令牌始终由中心生成。
    """

    expected_version: int = Field(ge=1, description="读取客户端时的并发版本")


class SitePayload(KnowledgePayload):
    """
    创建站点及别名；名称与域名也参与全局歧义检查。
    """

    name: str = Field(min_length=1, max_length=120, description="站点名称")
    domain: str = Field(min_length=3, max_length=253, description="主域名，不含协议或路径")
    aliases: list[str] = Field(default_factory=list, max_length=32, description="站点别名")
    enabled: bool = Field(default=True, description="是否启用本站知识")

    @field_validator("domain")
    @classmethod
    def canonical_domain(cls, value: str) -> str:
        """
        仅接受可作为独立站点标识的公开主机名。
        """
        parsed = urlsplit(public_url("https://" + value))
        if parsed.path != "/" or parsed.query:
            raise ValueError("站点域名不能包含路径或查询参数")
        return parsed.hostname

    @field_validator("aliases")
    @classmethod
    def canonical_aliases(cls, value: list[str]) -> list[str]:
        """
        保留显示文本，清理重复和空白，但不接受无效别名。
        """
        result = {}
        for item in value:
            label = item.strip()
            if not label or len(label) > 120 or any(ord(char) < 32 for char in label):
                raise ValueError("站点别名长度需为 1–120 且不能包含控制字符")
            result.setdefault(normalized_name(label), label)
        return list(result.values())


class SiteUpdate(SitePayload):
    """
    更新站点的完整表单，使用版本检查保护并发编辑。
    """

    expected_version: int = Field(ge=1, description="读取表单时的并发版本")


class SourcePayload(KnowledgePayload):
    """
    创建来源，只保存连接器支持的非敏感字段，不执行网络访问。
    """

    collection_id: str = Field(pattern=r"^[a-f0-9]{32}$", description="所属集合标识")
    site_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$", description="可选站点标识")
    name: str = Field(min_length=1, max_length=120, description="来源名称")
    connector: Literal["manual", "jsonl", "github", "http", "nexusphp"] = Field(description="连接器类型")
    url: str = Field(default="", max_length=2048, description="不含凭据的 HTTPS 地址")
    repository: str = Field(default="", max_length=160, description="GitHub 仓库所有者及名称")
    branch: str = Field(default="main", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$", description="指定仓库分支")
    paths: list[str] = Field(default_factory=list, max_length=100, description="允许的相对页面或仓库路径")
    selector: str = Field(default="", max_length=200, description="正文选择器")
    refresh_minutes: int = Field(default=0, ge=0, le=525600, description="刷新间隔分钟，零表示仅手动")
    enabled: bool = Field(default=False, description="是否允许后续采集")

    @field_validator("url")
    @classmethod
    def canonical_url(cls, value: str) -> str:
        """
        避免把明文认证信息或本地地址保存成普通来源字段。
        """
        return public_url(value)

    @field_validator("paths")
    @classmethod
    def canonical_paths(cls, value: list[str]) -> list[str]:
        """
        来源路径不能跳出根目录、切换主机或夹带认证查询参数。
        """
        result = []
        for raw in value:
            path = raw.strip()
            parsed = urlsplit(path)
            decoded = unquote(parsed.path)
            if (not path or len(path) > 512 or parsed.scheme or parsed.netloc or parsed.fragment
                    or decoded.startswith("/") or "\\" in decoded or ".." in decoded.split("/")
                    or any(ord(char) < 32 for char in path)):
                raise ValueError("来源路径必须为根目录内的相对路径")
            public_url("https://source.example/" + path)
            if path not in result:
                result.append(path)
        return result

    @model_validator(mode="after")
    def compatible_connector(self):
        """
        连接器参数明确且互相兼容，手工来源不能隐含自动抓取。
        """
        if self.connector == "github":
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) or not self.paths:
                raise ValueError("GitHub 来源需要明确仓库和路径白名单")
            if self.url or self.selector:
                raise ValueError("GitHub 来源使用仓库定位，不能附加网页参数")
        elif self.repository:
            raise ValueError("只有 GitHub 来源允许填写仓库")
        if self.connector in ("http", "nexusphp") and not self.url:
            raise ValueError("网页来源需要 HTTPS 地址")
        if self.connector == "nexusphp" and not self.site_id:
            raise ValueError("NexusPHP 来源需要绑定站点")
        if self.connector in ("manual", "jsonl") and (self.url or self.paths or self.selector or self.refresh_minutes):
            raise ValueError("手工和导入来源不接受网络参数或周期采集")
        return self


class SourceUpdate(SourcePayload):
    """
    来源使用版本保护更新；已有文档时不能改变知识集合或站点归属。
    """

    expected_version: int = Field(ge=1, description="读取表单时的并发版本")


class RevisionPayload(KnowledgePayload):
    """
    明确保存一份草稿；未知适用性和采集时间保持未知。
    """

    title: str = Field(min_length=1, max_length=300, description="知识标题")
    content: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=1, max_length=262144, description="保留原始空白的私有草稿正文")
    product: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9-]{0,63})?$", description="适用产品")
    version_scope: Literal["unknown", "all", "range"] = Field(default="unknown", description="版本适用范围")
    min_version: str | None = Field(default=None, max_length=32, description="明确稳定版本下界")
    max_version: str | None = Field(default=None, max_length=32, description="明确稳定版本上界")
    source_url: str = Field(default="", max_length=2048, description="不含凭据的出处")
    source_commit: str = Field(default="", pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})?$", description="明确的来源快照")
    fetched_at: datetime | None = Field(default=None, description="已知的实际采集时间，未知为 null")
    valid_until: datetime | None = Field(default=None, description="规则有效期截止时间")

    @field_validator("content")
    @classmethod
    def nonempty_content(cls, value: str) -> str:
        """
        拒绝空白正文，但保留 Markdown 缩进、换行和原始尾部。
        """
        if not value.strip():
            raise ValueError("知识正文不能为空")
        return value

    @field_validator("source_url")
    @classmethod
    def canonical_url(cls, value: str) -> str:
        """
        出处不能携带可回显的认证参数。
        """
        return public_url(value)

    @field_validator("min_version", "max_version")
    @classmethod
    def canonical_version(cls, value: str | None) -> str | None:
        """
        稳定版本使用一致表示，便于后续比较。
        """
        return stable_version(value)

    @field_validator("fetched_at", "valid_until")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        """
        拒绝无时区时间，避免把未知时区规则判断成仍有效。
        """
        if value is not None and value.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="after")
    def compatible_scope(self):
        """
        适用范围与版本边界一致，有效期不能早于明确的采集时间。
        """
        bounds = (self.min_version, self.max_version)
        if self.version_scope == "range" and not any(bounds):
            raise ValueError("版本范围至少需要一个边界")
        if self.version_scope != "range" and any(bounds):
            raise ValueError("只有明确版本范围可填写边界")
        if all(bounds) and tuple(map(int, bounds[0].split("."))) > tuple(map(int, bounds[1].split("."))):
            raise ValueError("版本下界不能大于上界")
        if self.fetched_at and self.valid_until and self.valid_until <= self.fetched_at:
            raise ValueError("有效期必须晚于采集时间")
        return self


class DocumentPayload(RevisionPayload):
    """
    在固定手工来源内创建稳定文档身份及第一份草稿。
    """

    source_id: str = Field(pattern=r"^[a-f0-9]{32}$", description="所属手工来源")
    external_id: str = Field(min_length=1, max_length=512, description="来源内稳定的文档标识")


class RevisionCreate(RevisionPayload):
    """
    使用当前文档版本追加草稿，不修改旧修订正文。
    """

    expected_version: int = Field(ge=1, description="读取文档时的并发版本")
