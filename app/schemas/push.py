"""
私有推送中转的注册、配对与有界消息协议。
"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PushEnrollment(BaseModel):
    """
    首次登记容器来源，不允许用 UID 指定已有实例空间或替换他人的密钥。
    """

    model_config = ConfigDict(extra="forbid")
    user_uid: str = Field(min_length=16, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$", description="既有 MoviePilot 用户唯一 ID，仅关联来源，不作为身份验证")


class PushRotation(BaseModel):
    """
    已持有当前凭据的容器提交先行持久化的新随机凭据，支持响应丢失后重试。
    """

    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^mpa_[a-zA-Z0-9_-]{43}$", description="容器安全随机生成的256位新凭据，仅经 HTTPS 正文提交")


class PushOwner(BaseModel):
    """
    后端鉴权后提交的稳定用户身份及精确实例地址。
    """

    model_config = ConfigDict(extra="forbid")
    owner: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="MoviePilot 用户的不可变通知身份")
    origin: str = Field(min_length=1, max_length=2048, description="接收者连接的实例地址")

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        """
        只允许普通网页地址，通知不能携带用户名、密码或片段。
        """
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("实例地址必须是无凭据、查询参数和片段的 HTTP(S) 地址")
        return value.rstrip("/")


class PushRegistration(PushOwner):
    """
    APNs 注册由 MoviePilot 后端代理，客户端不能选择其他接收用户。
    """

    installation: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="本次安装的随机标识")
    token: str = Field(min_length=32, max_length=512, pattern=r"^[0-9a-fA-F]+$", description="系统返回的 APNs 设备令牌")
    topic: str = Field(min_length=1, max_length=255, description="签名对应的 Bundle ID")
    environment: Literal["sandbox", "production"] = Field(description="签名对应的 APNs 环境")


class PushPairing(PushOwner):
    """
    网页配对归属发起配对的 App 安装，App 注销时一并解除绑定。
    """

    installation: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="发起绑定的 App 安装标识")


class PushRemoval(BaseModel):
    """
    后端按当前用户与安装移除绑定。
    """

    model_config = ConfigDict(extra="forbid")
    owner: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="当前用户的不可变通知身份")
    installation: str = Field(min_length=16, max_length=64, description="要注销的安装标识")


class PushMessage(BaseModel):
    """
    单用户消息不提供隐式广播，跳转仅允许实例内相对路由。
    """

    model_config = ConfigDict(extra="forbid")
    owner: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="接收用户的不可变通知身份")
    title: str = Field(default="MoviePilot", max_length=256, description="通知标题")
    body: str = Field(default="", max_length=3000, description="纯文本消息正文")
    route: str = Field(default="/?shotcut=message", max_length=2048, description="实例内跳转路由")
    image: str | None = Field(default=None, max_length=2048, description="由接收设备加载的 HTTP(S) 海报地址，旧客户端可不提供")

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        """
        只传递无凭据的网页图片，不允许脚本、内嵌文档或本地文件引用。
        """
        if value is None:
            return None
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError("海报必须是无凭据的 HTTP(S) 地址")
        return value

    @field_validator("route")
    @classmethod
    def validate_route(cls, value: str) -> str:
        """
        拒绝协议、双斜线和反斜线，防止通知点击跳到其他站点。
        """
        if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError("通知只能跳转到实例内路由")
        return value


class WebSubscription(BaseModel):
    """
    保存浏览器 PushSubscription 的必要公开密钥。
    """

    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(max_length=4096, description="浏览器推送服务端点")
    keys: dict[str, str] = Field(description="浏览器生成的 p256dh 与 auth 密钥")
    expirationTime: int | None = Field(default=None, description="浏览器提供的过期时间")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """
        仅允许已知浏览器推送服务的 HTTPS 端点，禁止将中转器用作任意出站代理。
        """
        parsed = urlsplit(value)
        hosts = ("web.push.apple.com", "push.services.mozilla.com", "fcm.googleapis.com", "notify.windows.com")
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 443} or not any(host == item or host.endswith("." + item) for item in hosts):
            raise ValueError("不支持的浏览器推送端点")
        return value

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, value: dict[str, str]) -> dict[str, str]:
        """
        限制密钥字段和编码长度，实际曲线校验由 Web Push 加密库执行。
        """
        if set(value) != {"p256dh", "auth"} or any(not 16 <= len(item) <= 256 for item in value.values()):
            raise ValueError("浏览器订阅密钥无效")
        return value


class WebRegistration(BaseModel):
    """
    主屏幕网页消费一次性绑定码，不能自行提交用户或实例身份。
    """

    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=128, description="十分钟内有效的一次性绑定码")
    installation: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="网页安装标识")
    subscription: WebSubscription = Field(description="当前浏览器的推送订阅")


class WebManagement(BaseModel):
    """
    仅持有设备管理令牌的网页可以测试和解除自己的绑定。
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=32, max_length=64, description="服务端分配的设备标识")
    secret: str = Field(min_length=32, max_length=128, description="绑定时一次返回的设备管理令牌")
