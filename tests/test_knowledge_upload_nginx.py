"""
用实际 Nginx 验证导入专用路由突破普通后台 1 MiB 限额并直接流向后端。
"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

import pytest

from test_admin_nginx import free_port


def test_real_nginx_streams_private_upload_before_body_complete(tmp_path):
    """
    只发送第一块后后端就必须收到数据，避免把仅检查配置误当作未落临时文件的证明。
    """
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("本机未安装 Nginx")
    first = threading.Event()
    received = []

    class UploadBackend(BaseHTTPRequestHandler):
        """
        观察第一块到达时机与完整请求头，不写上传内容到文件或日志。
        """

        def do_POST(self):
            """
            先通知观察者再等待剩余正文，证明代理没有等待整个包接收完毕。
            """
            total = int(self.headers["Content-Length"])
            initial = self.rfile.read(1024)
            first.set()
            body = initial + self.rfile.read(total - len(initial))
            received.append((len(body), self.headers.get("X-CSRF-Token"), self.headers.get("Cookie")))
            self.send_response(200)
            self.send_header("Cache-Control", "public,max-age=900")
            self.end_headers()
            self.wfile.write(b"received")

        def log_message(self, *_):
            """
            测试中不输出访问日志或请求头。
            """

    source = (Path(__file__).parents[1] / "docker/nginx.conf").read_text()
    exact = re.findall(r"\t\tlocation = /admin/api/knowledge/imports/upload \{.*?\n\t\t\}", source, re.S)
    broad = re.findall(r"\t\tlocation \^~ /admin/ \{.*?\n\t\t\}", source, re.S)
    assert len(exact) == len(broad) == 1
    backend = ThreadingHTTPServer(("127.0.0.1", 0), UploadBackend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    config = tmp_path / "nginx.conf"
    config.write_text(f"""
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
                {exact[0]}
                {broad[0]}
                location / {{ return 200 'website'; }}
            }}
        }}
    """)
    validation = subprocess.run([nginx, "-t", "-p", str(tmp_path), "-c", str(config)], capture_output=True, text=True)
    assert validation.returncode == 0, validation.stderr
    process = subprocess.Popen([nginx, "-p", str(tmp_path), "-c", str(config)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    connection = None
    try:
        for _attempt in range(50):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/")
                response = connection.getresponse()
                assert response.read() == b"website"
                connection.close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("隔离 Nginx 未启动")
        body = b"x" * (2 * 1024 * 1024)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.putrequest("POST", "/admin/api/knowledge/imports/upload?source_id=synthetic&source_version=1")
        for key, value in {"Content-Length": str(len(body)), "Content-Type": "application/x-ndjson",
                           "X-CSRF-Token": "synthetic-csrf", "Cookie": "mp_admin_session=synthetic-session"}.items():
            connection.putheader(key, value)
        connection.endheaders()
        connection.send(body[:65536])
        assert first.wait(3), "代理等待完整正文，没有直接流向后端"
        connection.send(body[65536:])
        response = connection.getresponse()
        assert response.status == 200 and response.read() == b"received"
        assert response.getheader("Cache-Control") == "no-store"
        assert received == [(len(body), "synthetic-csrf", "mp_admin_session=synthetic-session")]
        assert not any(path.is_file() for path in (tmp_path / "body").rglob("*"))
    finally:
        if connection:
            connection.close()
        process.terminate()
        try:
            # 排空管道，避免 Nginx 退出日志阻塞；异常退出也回收测试自建的进程。
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)
