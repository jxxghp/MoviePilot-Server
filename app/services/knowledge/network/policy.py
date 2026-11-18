"""
批准的来源范围和连接时地址检查，不把一次表单 URL 校验当作 DNS 防护。
"""

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from app.schemas.knowledge import public_url

BOUND_FIELDS = ("collection_id", "site_id", "connector", "url", "repository", "branch", "paths", "selector")
TRANSITION_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "192.88.99.0/24", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32",
))


class NetworkError(RuntimeError):
    """
    出站失败只携带固定状态，不携带上游响应、地址、认证头或第三方异常。
    """

    def __init__(self, code: str):
        """所有调用点只传固定代码，避免记录异常时泄露秘密。"""
        super().__init__(code)
        self.code = code


def public_address(value: str) -> None:
    """
    拒绝非公开地址和可映射到其他网络的过渡地址，包括 DNS 与实际连接目标。
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise NetworkError("address_rejected") from None
    if (not address.is_global or address.is_reserved or address.is_multicast or "%" in value
            or any(address in network for network in TRANSITION_NETWORKS)):
        raise NetworkError("address_rejected")


def binding(source) -> str:
    """
    不受字段顺序影响地绑定批准范围；保存后的路径列表与请求模型统一处理。
    """
    values = {key: getattr(source, key) for key in BOUND_FIELDS}
    if isinstance(values["paths"], str):
        values["paths"] = json.loads(values["paths"])
    values["source_id"] = source.id
    return hashlib.sha256(json.dumps(values, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class SourceScope:
    """
    由当前中心来源创建的精确访问范围；不存在模型可覆盖的代理、TLS 或主机参数。
    """

    source_id: str
    binding: str
    connector: str
    host: str
    pages: tuple[str, ...]
    repository: str

    def validate(self, url: str) -> str:
        """
        每次请求和跳转都检查授权范围，字面 IP 也必须通过公开地址规则。
        """
        try:
            canonical = public_url(url)
            parsed = urlsplit(canonical)
            if parsed.hostname != self.host:
                raise ValueError("foreign_host")
            if self.connector == "github":
                path = unquote(parsed.path).casefold()
                if ("%" in path or any(part in {".", ".."} for part in path.split("/"))
                        or not path.startswith("/repos/" + self.repository.casefold() + "/")
                        or path[len("/repos/" + self.repository) + 1:].split("/")[0] not in {
                            "commits", "contents", "git",
                        }):
                    raise ValueError("foreign_repository")
                if "/git/" in path and not re.search(r"/git/(?:blobs|trees)/", path):
                    raise ValueError("unsupported_repository_api")
            elif canonical not in self.pages:
                raise ValueError("unapproved_page")
            try:
                address = ipaddress.ip_address(self.host)
            except ValueError:
                address = None
            if address is not None:
                public_address(str(address))
            return canonical
        except (ValueError, TypeError):
            raise NetworkError("target_rejected") from None


def source_scope(source) -> SourceScope:
    """
    GitHub 仅批准指定仓库 API，网页仅批准后台明确列出的页面，不自动信任站内链接。
    """
    if source.connector not in {"github", "http", "nexusphp"}:
        raise NetworkError("connector_unsupported")
    try:
        paths = json.loads(source.paths) if isinstance(source.paths, str) else source.paths
        if source.connector == "github":
            host, pages = "api.github.com", ()
        else:
            base = public_url(source.url)
            pages = tuple(dict.fromkeys([base, *(public_url(urljoin(base, path)) for path in paths)]))
            host = urlsplit(base).hostname
            if any(urlsplit(page).hostname != host for page in pages):
                raise ValueError("foreign_page")
        return SourceScope(source.id, binding(source), source.connector, host, pages, source.repository)
    except (ValueError, TypeError):
        raise NetworkError("source_configuration_invalid") from None
