"""
只读、有界、逐跳授权的 HTTPS 请求；不会使用环境代理、隐式 Cookie 或自动重定向。
"""

import asyncio
import contextlib
import re
import socket
import ssl
import threading
from dataclasses import dataclass, field
from urllib.parse import urljoin

import aiohttp
import certifi
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver
from pydantic import SecretStr

from app.services.knowledge.network.policy import NetworkError, SourceScope, public_address

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
FETCH_SECONDS = 15
_capacity = threading.BoundedSemaphore(2)
CONTENT_TYPES = {"text/html", "text/plain", "text/markdown", "application/xhtml+xml",
                 "application/json", "application/vnd.github+json"}


@dataclass(frozen=True)
class AuthMaterial:
    """解密值仅供批准范围的单次读取，调试表示不显示明文。"""

    source_id: str
    binding: str
    kind: str
    value: SecretStr = field(repr=False)


@dataclass(frozen=True)
class FetchResult:
    """有界响应数据交给连接器解析，调试表示不包含知识正文或上游头。"""

    status: int
    url: str
    content_type: str
    body: bytes = field(repr=False)
    etag: str | None = field(default=None, repr=False)
    modified: str | None = field(default=None, repr=False)
    charset: str | None = None


class PublicResolver(AbstractResolver):
    """只将经过公开地址检查的解析结果交给连接器，禁止检查后再次未受控解析。"""

    def __init__(self):
        """系统解析器由此实例独占，随请求客户端关闭。"""
        self.delegate = DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        """任一答案不合规就拒绝整组，兼容多地址回退而不留私网候选。"""
        rows = await self.delegate.resolve(host, port, family)
        if not rows or len(rows) > 16:
            raise NetworkError("dns_rejected")
        for row in rows:
            public_address(row["host"])
        return rows

    async def close(self):
        """关闭底层解析器，避免请求结束后残留解析资源。"""
        await self.delegate.close()


def public_socket(address):
    """即使客户端跳过字面 IP 的 resolver，创建连接时仍检查实际目标地址。"""
    family, kind, protocol, _canonical, endpoint = address
    public_address(endpoint[0])
    return socket.socket(family=family, type=kind, proto=protocol)


def tls_context():
    """使用受信 CA 和原始主机验证 TLS，不提供关闭校验的产品配置。"""
    return ssl.create_default_context(cafile=certifi.where())


def conditional(value: str | None) -> str | None:
    """上游缓存校验头也必须短小且不含换行，不接受任意请求头字典。"""
    if value is None:
        return None
    if not value or len(value) > 512 or any(not 32 <= ord(char) < 127 for char in value):
        raise NetworkError("conditional_header_invalid")
    return value


async def fetch(scope: SourceScope, url: str, auth: AuthMaterial | None = None,
                *, etag: str | None = None, modified: str | None = None) -> FetchResult:
    """
    每次 GET 的目标、认证、大小和时限均固定受控，连接器不能传代理或放宽 TLS。
    """
    current = scope.validate(url)
    headers = {"Accept": "text/html,text/plain,text/markdown,application/json",
               "Accept-Encoding": "identity", "User-Agent": "MoviePilot-Knowledge/1"}
    if auth is not None:
        if auth.source_id != scope.source_id or auth.binding != scope.binding:
            raise NetworkError("credential_scope_mismatch")
        value = auth.value.get_secret_value()
        if (auth.kind not in {"cookie", "bearer"} or not value or len(value) > 8192
                or any(not 32 <= ord(char) < 127 for char in value)):
            raise NetworkError("credential_invalid")
        headers["Cookie" if auth.kind == "cookie" else "Authorization"] = value if auth.kind == "cookie" else "Bearer " + value
    if conditional(etag):
        headers["If-None-Match"] = etag
    if conditional(modified):
        headers["If-Modified-Since"] = modified
    if not _capacity.acquire(blocking=False):
        raise NetworkError("fetch_busy")
    resolver = None
    connector = None
    try:
        async with asyncio.timeout(FETCH_SECONDS):
            resolver = PublicResolver()
            connector = aiohttp.TCPConnector(resolver=resolver, socket_factory=public_socket, ssl=tls_context(),
                                             use_dns_cache=False, force_close=True, limit=1, limit_per_host=1)
            timeout = aiohttp.ClientTimeout(total=10, connect=4, sock_connect=3, sock_read=4)
            async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.DummyCookieJar(),
                                             trust_env=False, auto_decompress=False, timeout=timeout) as client:
                for redirect in range(MAX_REDIRECTS + 1):
                    async with client.get(current, headers=headers, allow_redirects=False) as result:
                        if result.status in {301, 302, 303, 307, 308}:
                            location = result.headers.get("Location", "")
                            if not location or len(location) > 2048 or redirect == MAX_REDIRECTS:
                                raise NetworkError("redirect_rejected")
                            current = scope.validate(urljoin(current, location))
                            continue
                        if result.status == 304:
                            return FetchResult(304, current, "", b"")
                        if result.status in {401, 403}:
                            raise NetworkError("authentication_rejected")
                        if result.status == 429:
                            raise NetworkError("upstream_rate_limited")
                        if result.status != 200:
                            raise NetworkError("upstream_status")
                        if result.headers.get("Content-Encoding", "identity").lower() != "identity":
                            raise NetworkError("response_encoding_unsupported")
                        content_type = result.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                        if content_type not in CONTENT_TYPES:
                            raise NetworkError("response_type_unsupported")
                        charset = result.charset
                        if charset is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", charset):
                            raise NetworkError("response_charset_invalid")
                        if result.content_length is not None and result.content_length > MAX_BYTES:
                            raise NetworkError("response_too_large")
                        body = bytearray()
                        async for part in result.content.iter_chunked(32768):
                            if len(body) + len(part) > MAX_BYTES:
                                raise NetworkError("response_too_large")
                            body.extend(part)
                        return FetchResult(200, current, content_type, bytes(body),
                                           conditional(result.headers.get("ETag")),
                                           conditional(result.headers.get("Last-Modified")), charset)
    except TimeoutError:
        raise NetworkError("fetch_timeout") from None
    except (aiohttp.ClientError, OSError, ValueError):
        raise NetworkError("fetch_failed") from None
    finally:
        try:
            if connector is not None:
                with contextlib.suppress(Exception):
                    await connector.close()
            if resolver is not None:
                with contextlib.suppress(Exception):
                    await resolver.close()
        finally:
            _capacity.release()
