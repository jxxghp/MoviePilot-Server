"""通过实际 Nginx 验证管理、推送路径及资源不落入官网回退或缓存。"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time

import pytest


def free_port() -> int:
    """从操作系统取得一个用于隔离代理验证的空闲本机端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EchoBackend(BaseHTTPRequestHandler):
    """回显原始路径与测试请求头，验证私有入口的代理和防缓存边界。"""

    def do_GET(self):
        """模拟成功或鉴权拒绝响应，并回显代理收到的认证与来源信息。"""
        self.send_response(401 if self.path.endswith("/unauthorized") else 200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "public, max-age=900")
        self.send_header("X-Echo-Authorization", self.headers.get("Authorization", ""))
        self.send_header("X-Echo-Forwarded-For", self.headers.get("X-Forwarded-For", ""))
        self.send_header("X-Echo-Real-IP", self.headers.get("X-Real-IP", ""))
        self.end_headers()
        self.wfile.write(self.path.encode())

    def log_message(self, *_):
        """测试中不输出每个代理请求的访问日志。"""


@pytest.mark.parametrize("prefix", ["admin", "push", "knowledge", "recognize/mapping"])
def test_real_nginx_preserves_private_routes(tmp_path, prefix):
    """使用仓库原样 location，实测私有路径、静态资源、认证头和错误防缓存。"""
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("本机未安装 Nginx")
    source = (Path(__file__).parents[1] / "docker/nginx.conf").read_text()
    route_pattern = (
        r"\t\tlocation (?:= /knowledge|\^~ /knowledge/(?!v1/agent/)) \{.*?\n\t\t\}"
        if prefix == "knowledge" else
        rf"\t\tlocation (?:= /{prefix}|\^~ /{prefix}/) \{{.*?\n\t\t\}}"
    )
    blocks = re.findall(route_pattern, source, re.S)
    if prefix == "knowledge":
        agent_block = re.findall(r"\t\tlocation \^~ /knowledge/v1/agent/ \{.*?\n\t\t\}", source, re.S)
        assert len(agent_block) == 1 and 'proxy_set_header Authorization "";' in agent_block[0]
        blocks.extend(agent_block)
    assert len(blocks) == (1 if prefix == "recognize/mapping" else 3 if prefix == "knowledge" else 2)
    if prefix == "recognize/mapping":
        assert "proxy_connect_timeout 1s;" in blocks[0]
        assert "proxy_read_timeout 2s;" in blocks[0]
    backend = ThreadingHTTPServer(("127.0.0.1", 0), EchoBackend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    config = tmp_path / "nginx.conf"
    realip = tmp_path / "cloudflare-realip.conf"
    realip.write_text((Path(__file__).parents[1] / "docker/cloudflare-realip.conf").read_text())
    blocks = [block.replace('/etc/nginx/cloudflare-realip.conf', str(realip)) for block in blocks]
    config.write_text(f'''
        daemon off;
        master_process off;
        pid {tmp_path}/nginx.pid;
        error_log stderr;
        events {{ worker_connections 32; }}
        http {{
            access_log off;
            client_body_temp_path {tmp_path}/body;
            proxy_temp_path {tmp_path}/proxy;
            upstream moviepilot_server {{ server 127.0.0.1:{backend.server_port}; }}
            server {{
                listen 127.0.0.1:{port};
                {''.join(blocks)}
                location / {{ return 200 'website'; }}
                location ~* \\.(js|css|png)$ {{ return 200 'wrong-static-cache'; }}
            }}
        }}
    ''')
    validation = subprocess.run([nginx, "-t", "-p", str(tmp_path), "-c", str(config)], capture_output=True, text=True)
    assert validation.returncode == 0, validation.stderr
    process = subprocess.Popen([nginx, "-p", str(tmp_path), "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for attempt in range(50):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("GET", "/")
                response = connection.getresponse()
                assert response.read() == b"website"
                connection.close()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)
        else:
            pytest.fail("隔离 Nginx 未启动")
        suffixes = ("?check=1", "?case=/unauthorized") if prefix == "recognize/mapping" else (
            "/", "/users", "/api/auth/me?check=1", "/assets/main.js", "/logo.png", "/app/worker.js",
            "/v1/agent/search", "/unauthorized",
        )
        for suffix in suffixes:
            path = f"/{prefix}{suffix}"
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", path, headers={
                "Authorization": "Bearer test-private-credential", "X-Forwarded-For": "198.51.100.23",
            })
            response = connection.getresponse()
            assert response.status == (401 if suffix.endswith("/unauthorized") else 200)
            assert response.read().decode() == path
            assert response.getheader("Cache-Control") == "no-store"
            expected_authorization = "" if prefix == "knowledge" and suffix.startswith("/v1/agent/") else "Bearer test-private-credential"
            assert response.getheader("X-Echo-Authorization") == expected_authorization
            if prefix in {"push", "knowledge"}:
                assert response.getheader("X-Echo-Forwarded-For") == "127.0.0.1"
            connection.close()
        if prefix == "knowledge":
            for route in ("/knowledge/v1/search", "/knowledge/v1/agent/search"):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("POST", route, body=b"x" * 8193)
                response = connection.getresponse()
                assert response.status == 413 and response.getheader("Cache-Control") == "no-store"
                response.read()
                connection.close()
        if prefix == "recognize/mapping":
            return
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", f"/{prefix}")
        response = connection.getresponse()
        assert response.status == 308 and response.getheader("Location").endswith(f"/{prefix}/")
        response.read()
        connection.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("claimed", ["198.51.100.23", "2001:db8::23", ""])
def test_knowledge_nginx_restores_only_trusted_proxy_addresses(tmp_path, trusted, claimed):
    """真实代理只采纳受信连接的 CF 地址，直连伪造或缺少该头时保持实际连接地址。"""
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("本机未安装 Nginx")
    source = (Path(__file__).parents[1] / "docker/nginx.conf").read_text()
    routes = re.findall(r"\t\tlocation \^~ /knowledge/(?:v1/agent/)? \{.*?\n\t\t\}", source, re.S)
    assert len(routes) == 2
    realip = tmp_path / "cloudflare-realip.conf"
    realip.write_text(("set_real_ip_from 127.0.0.1;\n" if trusted else "") +
                      (Path(__file__).parents[1] / "docker/cloudflare-realip.conf").read_text())
    routes = [route.replace('/etc/nginx/cloudflare-realip.conf', str(realip)) for route in routes]
    backend = ThreadingHTTPServer(("127.0.0.1", 0), EchoBackend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    config = tmp_path / "nginx.conf"
    config.write_text(f'''
        daemon off;
        master_process off;
        pid {tmp_path}/nginx.pid;
        error_log stderr;
        events {{ worker_connections 32; }}
        http {{
            access_log off;
            client_body_temp_path {tmp_path}/body;
            proxy_temp_path {tmp_path}/proxy;
            upstream moviepilot_server {{ server 127.0.0.1:{backend.server_port}; }}
            server {{ listen 127.0.0.1:{port}; {''.join(routes)} }}
        }}
    ''')
    result = subprocess.run([nginx, "-t", "-p", str(tmp_path), "-c", str(config)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    process = subprocess.Popen([nginx, "-p", str(tmp_path), "-c", str(config)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _attempt in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("隔离 Nginx 未启动")
        for path in ("/knowledge/v1/agent/search", "/knowledge/v1/search"):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", path, headers={"CF-Connecting-IP": claimed, "X-Real-IP": "203.0.113.88",
                                                     "X-Forwarded-For": "203.0.113.99"})
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            expected = claimed if trusted and claimed else "127.0.0.1"
            assert response.getheader("X-Echo-Real-IP") == expected
            assert response.getheader("X-Echo-Forwarded-For") == expected
            connection.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)
