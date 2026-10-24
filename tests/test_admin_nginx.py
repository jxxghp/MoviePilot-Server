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
        self.end_headers()
        self.wfile.write(self.path.encode())

    def log_message(self, *_):
        """测试中不输出每个代理请求的访问日志。"""


@pytest.mark.parametrize("prefix", ["admin", "push"])
def test_real_nginx_preserves_private_routes(tmp_path, prefix):
    """使用仓库原样 location，实测私有路径、静态资源、认证头和错误防缓存。"""
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("本机未安装 Nginx")
    source = (Path(__file__).parents[1] / "docker/nginx.conf").read_text()
    blocks = re.findall(rf"\t\tlocation (?:= /{prefix}|\^~ /{prefix}/) \{{.*?\n\t\t\}}", source, re.S)
    assert len(blocks) == 2
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
        for suffix in ("/", "/users", "/api/auth/me?check=1", "/assets/main.js", "/logo.png", "/app/worker.js", "/unauthorized"):
            path = f"/{prefix}{suffix}"
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", path, headers={
                "Authorization": "Bearer test-private-credential", "X-Forwarded-For": "198.51.100.23",
            })
            response = connection.getresponse()
            assert response.status == (401 if suffix == "/unauthorized" else 200)
            assert response.read().decode() == path
            assert response.getheader("Cache-Control") == "no-store"
            assert response.getheader("X-Echo-Authorization") == "Bearer test-private-credential"
            if prefix == "push":
                assert response.getheader("X-Echo-Forwarded-For") == "127.0.0.1"
            connection.close()
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
