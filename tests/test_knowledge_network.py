"""
受控知识 HTTPS 客户端：公开地址规则、批准范围，以及只连接本机的真实 TLS 集成。
"""

import asyncio
import json
import socket
import ssl
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr

from app.schemas.knowledge import SourcePayload
from app.services.knowledge.network import client, policy


def scope(*, connector="http", paths=(), repository="example/repository"):
    """仅建立合成定位，域名由测试解析器导向本机，不访问公网。"""
    return policy.source_scope(SimpleNamespace(
        id="a" * 32, collection_id="b" * 32, site_id=None, connector=connector,
        url="" if connector == "github" else "https://docs.example/rules",
        repository=repository if connector == "github" else "", branch="main",
        paths=json.dumps(list(paths)), selector="main" if connector != "github" else "",
    ))


@pytest.mark.parametrize("address", [
    "127.0.0.1", "0.0.0.0", "10.0.0.1", "172.16.1.1", "192.168.1.1", "169.254.169.254",
    "100.100.100.200", "224.0.0.1", "255.255.255.255", "::1", "fc00::1", "fe80::1",
    "::ffff:8.8.8.8", "::ffff:127.0.0.1", "64:ff9b::808:808", "64:ff9b:1::1",
    "2002:0808:0808::", "2001:0:1234::1", "ff02::1", "fe80::1%en0", "not-an-address",
    "192.88.99.1", "4000::1",
])
def test_nonpublic_mapped_and_transition_addresses_are_rejected(address):
    """不能通过地址编码、过渡网络或组播绕开私网及云元数据拒绝策略。"""
    with pytest.raises(policy.NetworkError) as error:
        policy.public_address(address)
    assert error.value.code == "address_rejected"


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_ordinary_public_addresses_are_allowed_without_connecting(address):
    """公开地址可作为候选，但此单元测试不会真的创建连接。"""
    policy.public_address(address)


def test_scope_rejects_unapproved_pages_hosts_credentials_and_repository_traversal():
    """相同主机不代表任意页面获批，GitHub 也不能越出来源仓库或使用认证 URL。"""
    target = scope(paths=["faq"])
    assert target.validate("https://docs.example/faq") == "https://docs.example/faq"
    for url in ("https://docs.example/logout", "https://elsewhere.example/rules",
                "http://docs.example/rules", "https://docs.example:8443/rules",
                "https://docs.example/rules?token=synthetic-value", "https://user:pass@docs.example/rules"):
        with pytest.raises(policy.NetworkError):
            target.validate(url)
    github = scope(connector="github", paths=["README.md"])
    assert github.validate("https://api.github.com/repos/example/repository/contents/README.md?ref=123")
    for url in ("https://api.github.com/repos/other/private/contents/README.md",
                "https://raw.githubusercontent.com/example/repository/main/README.md",
                "https://api.github.com/repos/example/repository/%2e%2e/elsewhere",
                "https://api.github.com/repos/example/repository/collaborators",
                "https://api.github.com/repos/example/repository/git/refs/heads/main"):
        with pytest.raises(policy.NetworkError):
            github.validate(url)
    for repository in ("../repository", "example/..", "example/.", "example/repository/extra"):
        with pytest.raises(ValueError):
            SourcePayload(collection_id="b" * 32, connector="github", name="invalid",
                          repository=repository, paths=["README.md"])


def test_resolver_rejects_mixed_answers_and_socket_rechecks_literals(monkeypatch):
    """解析到公开加私网混合答案时整体失败，字面地址绕过 DNS 仍会在 socket 层被拒绝。"""
    class Answers:
        """只返回固定地址的合成系统解析器，不触发真实 DNS。"""

        async def resolve(self, host, port, family):
            """故意混合安全和私网候选，不能只检查列表第一项。"""
            return [{"host": "8.8.8.8"}, {"host": "127.0.0.1"}]

        async def close(self):
            """合成解析器无外部资源。"""

    async def scenario():
        """沿真正 PublicResolver 执行检查，并关闭其委托。"""
        monkeypatch.setattr(client, "DefaultResolver", Answers)
        resolver = client.PublicResolver()
        with pytest.raises(policy.NetworkError):
            await resolver.resolve("docs.example", 443)
        await resolver.close()
    asyncio.run(scenario())
    with pytest.raises(policy.NetworkError):
        client.public_socket((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)))


def certificates(tmp_path, *, hostname="docs.example"):
    """在外置临时目录生成仅供本测试的自签证书和私钥，不读取任何部署证书。"""
    private = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(private.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .sign(private, hashes.SHA256()))
    certfile, keyfile = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-key.pem"
    certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    keyfile.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(certfile, keyfile)
    trusted = ssl.create_default_context(cafile=str(certfile))
    return server, trusted


async def local_server(monkeypatch, tmp_path, handler, *, certificate_hostname="docs.example"):
    """
    只在测试中将受控解析结果端口映射到本机 TLS 服务，并保留 Host / SNI 与证书验证。
    """
    server, trusted = certificates(tmp_path, hostname=certificate_hostname)
    sni = []

    def hostname(_socket, name, _context):
        """记录真实 TLS 握手的 SNI，用于证明没有把主机替换成字面 IP。"""
        sni.append(name)

    server.set_servername_callback(hostname)
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    checked = []

    async def resolve(_self, host, port=0, family=socket.AF_INET):
        """替换系统 DNS；测试请求不会到达公网，也不改产品允许范围配置。"""
        assert host == "docs.example"
        return [{"hostname": host, "host": "127.0.0.1", "port": site._server.sockets[0].getsockname()[1],
                 "family": socket.AF_INET, "proto": socket.IPPROTO_TCP, "flags": socket.AI_NUMERICHOST}]

    def local_address(address):
        """测试专用地址闸门只放行本机，独立单元测试已覆盖未经替换的真实拒绝规则。"""
        assert address == "127.0.0.1"
        checked.append(address)

    monkeypatch.setattr(client.DefaultResolver, "resolve", resolve)
    monkeypatch.setattr(client, "public_address", local_address)
    monkeypatch.setattr(client, "tls_context", lambda: trusted)
    return runner, sni, checked, port


def test_real_tls_preserves_sni_auth_scope_no_environment_proxy_and_no_cookie_jar(monkeypatch, tmp_path):
    """真实 TLS 握手保持原域名，凭据只送批准页面，不读取环境代理或接纳响应 Cookie。"""
    async def scenario():
        """一组同主机显式页面跳转，确认每一跳都经历地址检查。"""
        requests = []

        async def handler(request):
            """回显小段合成文档，服务器仅记录本测试的 Host 和认证值。"""
            requests.append((request.path, request.host, request.headers.get("Cookie"), request.headers.get("Authorization")))
            if request.path == "/start":
                return web.Response(status=302, headers={"Location": "/target", "Set-Cookie": "surprise=unapproved"})
            if request.path == "/foreign":
                return web.Response(status=302, headers={"Location": "https://foreign.example/target"})
            return web.Response(text="合成知识正文", headers={"ETag": '"synthetic-v1"'})

        runner, sni, checked, _port = await local_server(monkeypatch, tmp_path, handler)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        target = scope(paths=["start", "target", "foreign"])
        auth = client.AuthMaterial(target.source_id, target.binding, "cookie", SecretStr("session=synthetic-only"))
        try:
            result = await client.fetch(target, "https://docs.example/start", auth)
            assert result.body.decode() == "合成知识正文" and result.etag == '"synthetic-v1"' and result.charset == "utf-8"
            assert [row[2] for row in requests] == ["session=synthetic-only"] * 2
            assert [row[1] for row in requests] == ["docs.example"] * 2
            assert sni == ["docs.example"] * 2 and len(checked) == 4
            assert "合成知识正文" not in repr(result) and "synthetic-only" not in repr(auth)
            requests.clear()
            await client.fetch(target, "https://docs.example/start")
            assert [row[2] for row in requests] == [None, None]
            with pytest.raises(policy.NetworkError) as error:
                await client.fetch(target, "https://docs.example/foreign", auth)
            assert error.value.code == "target_rejected"
            wrong = client.AuthMaterial("f" * 32, target.binding, "cookie", auth.value)
            with pytest.raises(policy.NetworkError) as error:
                await client.fetch(target, "https://docs.example/target", wrong)
            assert error.value.code == "credential_scope_mismatch"
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_real_tls_refuses_untrusted_certificate_and_bounds_responses(monkeypatch, tmp_path):
    """证书校验不能关闭；重定向、大小、编码、状态和总时间均由真实响应驱动验证。"""
    original_tls = client.tls_context

    async def scenario():
        """不对网络客户端返回对象做 mock，故障来自本机 TLS 服务。"""
        async def handler(request):
            """提供有限、可重复的合成异常响应，不触碰真实网站。"""
            case = request.path.strip("/")
            if case == "loop":
                return web.Response(status=302, headers={"Location": "/loop"})
            if case == "slow":
                await asyncio.sleep(0.2)
            if case == "gzip":
                return web.Response(body=b"not-actually-decompressed", headers={"Content-Encoding": "gzip", "Content-Type": "text/plain"})
            if case == "large":
                return web.Response(body=b"x" * (client.MAX_BYTES + 1), content_type="text/plain")
            if case == "stream":
                response = web.StreamResponse(headers={"Content-Type": "text/plain"})
                await response.prepare(request)
                try:
                    for _ in range(client.MAX_BYTES // 32768 + 1):
                        await response.write(b"x" * 32768)
                    await response.write_eof()
                except ConnectionResetError:
                    pass
                return response
            if case == "binary":
                return web.Response(body=b"binary", content_type="application/octet-stream")
            if case == "unchanged":
                return web.Response(status=304)
            if case in {"401", "429", "500"}:
                return web.Response(status=int(case), text="must-not-echo-secret-response")
            return web.Response(text="synthetic")

        runner, _sni, _checked, _port = await local_server(monkeypatch, tmp_path, handler)
        target = scope(paths=["loop", "slow", "gzip", "large", "stream", "binary", "unchanged", "401", "429", "500"])
        trusted = client.tls_context
        try:
            monkeypatch.setattr(client, "tls_context", original_tls)
            with pytest.raises(policy.NetworkError) as error:
                await client.fetch(target, "https://docs.example/rules")
            assert error.value.code == "fetch_failed"
            monkeypatch.setattr(client, "tls_context", trusted)
            for case, expected in (("loop", "redirect_rejected"), ("gzip", "response_encoding_unsupported"),
                                   ("large", "response_too_large"), ("stream", "response_too_large"),
                                   ("binary", "response_type_unsupported"), ("401", "authentication_rejected"),
                                   ("429", "upstream_rate_limited"), ("500", "upstream_status")):
                with pytest.raises(policy.NetworkError) as error:
                    await client.fetch(target, "https://docs.example/" + case)
                assert error.value.code == expected
                assert "must-not-echo" not in str(error.value)
            result = await client.fetch(target, "https://docs.example/unchanged")
            assert result.status == 304 and result.body == b""
            monkeypatch.setattr(client, "FETCH_SECONDS", 0.05)
            with pytest.raises(policy.NetworkError) as error:
                await client.fetch(target, "https://docs.example/slow")
            assert error.value.code == "fetch_timeout"
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_actual_fetch_capacity_and_cancellation_release_after_client_closes(monkeypatch, tmp_path):
    """两个实际连接占满容量，第三个被拒绝；取消会关闭客户端并恢复后续请求容量。"""
    async def scenario():
        """等待服务端已接收两个请求后才取消，避免只测尚未启动的任务。"""
        gate, started = asyncio.Event(), asyncio.Event()
        count = 0

        async def handler(_request):
            """暂存两个实际入站连接，待检查完成再释放响应。"""
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            await gate.wait()
            return web.Response(text="synthetic")

        runner, _sni, _checked, _port = await local_server(monkeypatch, tmp_path, handler)
        target = scope()
        tasks = [asyncio.create_task(client.fetch(target, "https://docs.example/rules")) for _ in range(2)]
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            with pytest.raises(policy.NetworkError) as error:
                await client.fetch(target, "https://docs.example/rules")
            assert error.value.code == "fetch_busy"
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            gate.set()
            assert (await tasks[1]).status == 200
            assert (await client.fetch(target, "https://docs.example/rules")).status == 200
        finally:
            gate.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.cleanup()
    asyncio.run(scenario())


def test_trusted_certificate_for_another_hostname_is_still_rejected(monkeypatch, tmp_path):
    """证书签发者已信任也必须验证目标域名，不能把仅有 CA 检查当作 TLS 身份校验。"""
    async def scenario():
        """使用本机签发的其他域名证书，握手必须在发送认证头前失败。"""
        received = []

        async def handler(request):
            """若请求竟能到达则记录，便于发现不正确的主机校验放宽。"""
            received.append(request.path)
            return web.Response(text="synthetic")

        runner, _sni, _checked, _port = await local_server(
            monkeypatch, tmp_path, handler, certificate_hostname="wrong.example",
        )
        target = scope()
        auth = client.AuthMaterial(target.source_id, target.binding, "bearer", SecretStr("synthetic-token"))
        try:
            with pytest.raises(policy.NetworkError) as error:
                await client.fetch(target, "https://docs.example/rules", auth)
            assert error.value.code == "fetch_failed" and received == []
        finally:
            await runner.cleanup()
    asyncio.run(scenario())
