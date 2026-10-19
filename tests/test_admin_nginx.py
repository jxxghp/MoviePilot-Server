"""通过实际 Nginx 验证管理路径、静态资源和 API 不落入官网回退或缓存。"""

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
    """返回收到的原始路径，以证明 Nginx 未丢失管理入口前缀与查询参数。"""

    def do_GET(self):
        """模拟上游响应和可被覆盖的缓存头。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "public, max-age=900")
        self.end_headers()
        self.wfile.write(self.path.encode())

    def log_message(self, *_):
        """测试中不输出每个代理请求的访问日志。"""


def test_real_nginx_preserves_admin_routes(tmp_path):
    """使用仓库原样管理 location，实际发送页面、API、JS 和官网请求。"""
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("本机未安装 Nginx")
    source = (Path(__file__).parents[1] / "docker/nginx.conf").read_text()
    blocks = re.findall(r"\t\tlocation (?:= /admin|\^~ /admin/) \{.*?\n\t\t\}", source, re.S)
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
        for path in ("/admin/", "/admin/users", "/admin/api/auth/me?check=1", "/admin/assets/main.js", "/admin/logo.png"):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 200
            assert response.read().decode() == path
            assert response.getheader("Cache-Control") == "no-store"
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/admin")
        response = connection.getresponse()
        assert response.status == 308 and response.getheader("Location").endswith("/admin/")
        response.read()
        connection.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)
