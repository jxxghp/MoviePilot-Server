"""用真实 Nginx 和隔离上游验证 CookieCloud 限流、来源信任与脱敏监控。"""

from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit

import pytest

from app.services.cookiecloud import Monitor
from test_admin_nginx import free_port


class CookieBackend(BaseHTTPRequestHandler):
    """模拟 CookieCloud 端到端密文上传与各种方法拉取，不访问真实用户数据。"""

    def respond(self):
        """记录原样协议并返回上游缓存/CORS 头，观察代理是否正确覆盖。"""
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.received.append((self.command, self.path, body, dict(self.headers)))
        path = urlsplit(self.path).path
        found = re.fullmatch(r"/cookiecloud/(?:get/[^/]+/?|update)", path, re.I) and "/missing" not in path
        self.send_response(204 if self.command == "OPTIONS" else 200 if found else 404)
        self.send_header("Cache-Control", "public, max-age=900")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,POST,OPTIONS")
        self.end_headers()
        if self.command not in {"HEAD", "OPTIONS"}:
            self.wfile.write(b'{"encrypted":"synthetic-ciphertext"}')

    def do_GET(self):
        """模拟扩展和 MoviePilot 的密文拉取。"""
        self.respond()

    def do_HEAD(self):
        """保留 Express 自动支持的 HEAD 行为。"""
        self.respond()

    def do_POST(self):
        """模拟上传与携带解密密码的 POST 拉取。"""
        self.respond()

    def do_OPTIONS(self):
        """模拟浏览器跨域预检。"""
        self.respond()

    def log_message(self, *_):
        """禁止测试上游输出包含 KEY 的原始请求日志。"""


@contextmanager
def gateway(directory, *, trusted=True):
    """启动仓库原样限流配置，仅替换端口、日志路径和测试可信代理名单。"""
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("本机未安装 Nginx")
    source = (Path(__file__).parents[1] / "docker/nginx.conf").read_text()
    security = source[source.index("\tlog_format cookiecloud_security"):source.index("\n\tinclude\t   mime.types;")]
    routes = re.findall(r"\t\tlocation \^~ /cookiecloud/ \{.*?\n\t\t\}", source, re.S)
    assert len(routes) == 1
    realip = directory / "realip.conf"
    realip.write_text(("set_real_ip_from 127.0.0.1;\n" if trusted else "") +
                      (Path(__file__).parents[1] / "docker/cloudflare-realip.conf").read_text())
    logfile = directory / "security.log"
    backend = ThreadingHTTPServer(("127.0.0.1", 0), CookieBackend)
    backend.received = []
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    route = (routes[0].replace("/etc/nginx/cloudflare-realip.conf", str(realip))
             .replace("/var/log/nginx/cookiecloud-security.log", str(logfile))
             .replace("127.0.0.1:8088", f"127.0.0.1:{backend.server_port}"))
    config = directory / "nginx.conf"
    config.write_text(f"""
        daemon off;
        master_process off;
        pid {directory}/nginx.pid;
        error_log stderr;
        events {{ worker_connections 64; }}
        http {{
            access_log off;
            client_body_temp_path {directory}/body;
            proxy_temp_path {directory}/proxy;
            {security}
            server {{
                listen 127.0.0.1:{port};
                {route}
                location / {{ return 200 'website'; }}
                location ~* \\.(js|css|png)$ {{ return 200 'wrong-static-cache'; }}
            }}
        }}
    """)
    result = subprocess.run([nginx, "-t", "-p", str(directory), "-c", str(config)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    process = subprocess.Popen([nginx, "-p", str(directory), "-c", str(config)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _attempt in range(50):
            try:
                if request(port, "/")[0] == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("隔离 Nginx 未启动")
        yield port, backend, logfile
    finally:
        process.terminate()
        _stdout, errors = process.communicate(timeout=5)
        assert b"synthetic-secret" not in errors
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)


def request(port, path, method="GET", *, headers=None, body=None):
    """执行隔离代理请求并完整消费响应，使日志能在下一请求前落盘。"""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_cookiecloud_preserves_upload_pull_preflight_and_privacy(tmp_path):
    """上传/GET/HEAD/POST 拉取保持兼容，静态后缀和敏感字段不会泄露或进入缓存。"""
    with gateway(tmp_path) as (port, backend, logfile):
        # CORS 预检不消耗限流配额。
        for _ in range(15):
            assert request(port, "/cookiecloud/get/synthetic-secret.js", "OPTIONS")[0] == 204
        body = json.dumps({"uuid": "synthetic-secret", "encrypted": "synthetic-encrypted-value"})
        status, headers, _ = request(port, "/cookiecloud/update", "POST", body=body)
        assert status == 200 and backend.received[-1][2].decode() == body
        for index, method in enumerate(("GET", "HEAD", "POST")):
            path = f"/cookiecloud/get/synthetic-secret-{method}.js?password=synthetic-secret-query"
            address = f"2001:db8::{index + 7}"
            payload = '{"password":"synthetic-secret-body"}' if method == "POST" else None
            status, headers, data = request(port, path, method, body=payload, headers={
                "CF-Connecting-IP": address, "X-Real-IP": "198.51.100.9",
                "X-Forwarded-For": "198.51.100.8", "Cookie": "synthetic-secret-cookie"})
            assert status == 200 and headers["Cache-Control"] == "no-store"
            assert headers["Access-Control-Allow-Origin"] == "*"
            assert "Retry-After" not in headers
            assert data == (b"" if method == "HEAD" else b'{"encrypted":"synthetic-ciphertext"}')
            observed = backend.received[-1]
            assert observed[:2] == (method, path)
            assert observed[3]["X-Real-IP"] == observed[3]["X-Forwarded-For"] == address
        entries = [json.loads(line) for line in logfile.read_text().splitlines()]
        assert {entry["operation"] for entry in entries} == {"pull", "update"}
        assert "synthetic-secret" not in logfile.read_text()
        assert "synthetic-encrypted-value" not in logfile.read_text()


def test_cookiecloud_key_limit_covers_methods_encoding_and_rotating_ips(tmp_path):
    """同一 KEY 换 IP、请求方法、大小写路由或 URI 编码仍共享拉取配额。"""
    with gateway(tmp_path) as (port, backend, _logfile):
        for index, method in enumerate(("GET", "POST", "HEAD", "GET", "POST", "HEAD", "GET")):
            path = "/cookiecloud/GET/synthetic-secret" if index % 2 else "/cookiecloud/get/synthetic-%73ecret"
            status, headers, _ = request(port, path, method, headers={"CF-Connecting-IP": f"198.51.100.{index + 1}"})
            assert status == (200 if index == 0 else 429)
        assert len(backend.received) == 1
        assert headers["Retry-After"] == "60" and headers["Cache-Control"] == "no-store"
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert request(port, "/cookiecloud/get/another-key", headers={"CF-Connecting-IP": "198.51.100.8"})[0] == 200


def test_cookiecloud_normalizes_basename_aliases_and_preserves_query_encoding(tmp_path):
    """代理和 KEY 限流使用同一规范化路径，编码分隔符与点段不能借用 basename 绕过配额。"""
    with gateway(tmp_path) as (port, backend, logfile):
        assert request(port, "/cookiecloud/get/synthetic-secret", headers={"CF-Connecting-IP": "198.51.100.1"})[0] == 200
        # 原始 URI 若转给 Express，prefix%2fsecret 会被 basename() 当成 secret 拉取。
        for index, path in enumerate(("/cookiecloud/get/prefix%2fsynthetic-secret", "/%63ookiecloud/get/prefix%2Fsynthetic-secret")):
            status, headers, _data = request(port, path, headers={"CF-Connecting-IP": f"198.51.100.{index + 2}"})
            assert status == 404 and headers["Cache-Control"] == "no-store"
        assert backend.received[-1][1] == "/cookiecloud/get/prefix/synthetic-secret"
        assert request(port, "/cookiecloud/get/..%2Fget%2Fsynthetic-secret", headers={"CF-Connecting-IP": "198.51.100.4"})[0] == 429
        assert request(port, "/cookiecloud/get/another-key?data=%2f", headers={"CF-Connecting-IP": "198.51.100.5"})[0] == 200
        assert backend.received[-1][1] == "/cookiecloud/get/another-key?data=%2f"
        assert "synthetic-secret" not in logfile.read_text()


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("ipv6", [True, False])
def test_cookiecloud_trusts_only_cloudflare_for_client_limit(tmp_path, trusted, ipv6):
    """真实来源隔离 IPv4/IPv6 用户；直连伪造 CF/XFF 头不能绕过每 IP 配额。"""
    with gateway(tmp_path, trusted=trusted) as (port, backend, logfile):
        for index in range(12):
            claimed = f"2001:db8::{index + 1}" if ipv6 else f"198.51.100.{index + 1}"
            status, _headers, _ = request(port, f"/cookiecloud/get/key-{index}", headers={
                "CF-Connecting-IP": claimed, "X-Forwarded-For": claimed, "X-Real-IP": claimed})
            assert status == (429 if not trusted and index > 0 else 200)
        assert len(backend.received) == (12 if trusted else 1)
        if not trusted:
            assert {json.loads(line)["ip"] for line in logfile.read_text().splitlines()} == {"127.0.0.1"}


def test_cookiecloud_real_rejections_trigger_sanitized_alert(tmp_path):
    """请求真正被代理拒绝后，脱敏日志可直接触发异常访问告警。"""
    with gateway(tmp_path) as (port, _backend, logfile):
        statuses = [request(port, f"/cookiecloud/get/missing-synthetic-secret-{index}")[0] for index in range(21)]
        assert statuses.count(404) == 1 and statuses.count(429) == 20
        monitor = Monitor()
        alerts = [alert for line in logfile.read_bytes().splitlines()
                  for alert in monitor.observe(line, time.time())]
        assert len(alerts) == 1 and alerts[0]["reason"] == "rate_limited"
        assert alerts[0]["scope"] == "127.0.0.1"
        assert "synthetic-secret" not in json.dumps(alerts)
