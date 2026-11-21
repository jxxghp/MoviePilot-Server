"""验证异常访问聚合、内存边界、日志轮转和脱敏 Webhook 重试。"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import time

import httpx
import pytest

from app.services import cookiecloud
from app.services.cookiecloud import Alerts, LogReader, Monitor


def event(now, *, address="198.51.100.1", status=404, operation="pull", **extras):
    """创建仅用于本地验证的代理安全事件。"""
    return json.dumps({"time": now, "ip": address, "status": status, "operation": operation, **extras}).encode()


def test_missing_key_alert_cooldown_window_and_distinct_reasons():
    """低于限流频率的持续 KEY 枚举也告警，重复触发冷却且不同异常互不抑制。"""
    monitor, alerts = Monitor(), []
    for index in range(20):
        alerts += monitor.observe(event(6000 + index * 2), 6000 + index * 2)
    assert [(item["reason"], item["count"]) for item in alerts] == [("missing_keys", 20)]
    assert monitor.observe(event(6040), 6040) == []
    for index in range(20):
        alerts += monitor.observe(event(6050 + index, status=429), 6050 + index)
    assert [item["reason"] for item in alerts] == ["missing_keys", "rate_limited"]
    assert monitor.observe(event(6301), 6301) == []
    for index in range(20):
        alerts += monitor.observe(event(7000 + index), 7000 + index)
    assert len(alerts) == 3 and alerts[-1]["reason"] == "missing_keys"


def test_distributed_scan_and_service_failures_have_global_alerts():
    """轮换 IP 的低频扫描与全局上游故障仍能被聚合发现。"""
    monitor, alerts = Monitor(), []
    for index in range(100):
        alerts += monitor.observe(event(6000, address=f"2001:db8::{index}"), 6000)
    assert len(alerts) == 1 and alerts[0]["scope"] == "global"
    assert alerts[0]["reason"] == "missing_keys" and alerts[0]["count"] == 100
    for index in range(10):
        alerts += monitor.observe(event(6010, address=f"2001:db8::{index}", status=502), 6010)
    assert alerts[-1]["reason"] == "upstream_errors" and alerts[-1]["scope"] == "global"


@pytest.mark.parametrize("line", [b"broken", b"[]", b"null", b"{\"time\":null}", b"\xff",
                                  event(1000), event(6010), event(6000, address="invalid"),
                                  event(6000, status=200), event(6000, operation="other")])
def test_invalid_expired_future_and_success_events_do_not_alert(line):
    """无效或无关日志不改变异常计数，也不将原始内容写入告警。"""
    monitor = Monitor()
    for _ in range(120):
        assert monitor.observe(line, 6000) == []
    assert not monitor.clients


def test_client_memory_cap_does_not_reset_global_detection(monkeypatch):
    """来源状态被淘汰时全局扫描计数仍连续，所有队列均有上限。"""
    monkeypatch.setattr(cookiecloud, "MAX_CLIENTS", 3)
    monitor, alerts = Monitor(), []
    for index in range(100):
        alerts += monitor.observe(event(6000, address=f"2001:db8::{index}"), 6000)
    assert len(monitor.clients) == 3 and len(alerts) == 1
    for index in range(500):
        monitor.observe(event(6000 + index * 60), 6000 + index * 60)
    assert len(monitor.global_window.buckets) <= 5
    assert all(len(window.buckets) <= 5 for window in monitor.clients.values())


def test_reader_handles_partial_lines_rotation_and_copytruncate(tmp_path):
    """未完成的 JSON 不丢失，rename 和缩短原文件后仍读取新记录。"""
    path = tmp_path / "security.log"
    path.write_bytes(b"first\npart")
    reader = LogReader(path)
    try:
        assert reader.read() == [b"first\n"]
        with path.open("ab") as stream:
            stream.write(b"ial\n")
        assert reader.read() == [b"partial\n"]
        path.rename(tmp_path / "security.log.1")
        path.write_bytes(b"rotated\n")
        assert reader.read() == []
        assert reader.read() == [b"rotated\n"]
        path.write_bytes(b"new\n")
        assert reader.read() == [b"new\n"]
    finally:
        reader.close()


def test_reader_bounds_batches_and_ignores_oversized_records(tmp_path):
    """限制每次读取量，超长错误行的片段不能变成有效告警。"""
    path = tmp_path / "security.log"
    path.write_bytes(b"x" * 5000 + b"\nfirst\nsecond\n")
    reader = LogReader(path)
    try:
        assert reader.read(limit=2) == [b"first\n"]
        assert reader.read(limit=2) == [b"second\n"]
    finally:
        reader.close()


def test_reader_keeps_records_when_rotated_file_is_not_created_yet(tmp_path):
    """轮转中暂时没有新文件时，本轮旧日志和之后追加的记录仍可处理。"""
    path = tmp_path / "security.log"
    path.write_bytes(b"first\n")
    reader = LogReader(path)
    try:
        assert reader.read() == [b"first\n"]
        rotated = tmp_path / "security.log.1"
        path.rename(rotated)
        with rotated.open("ab") as stream:
            stream.write(b"last\n")
        assert reader.read() == [b"last\n"]
        path.write_bytes(b"new\n")
        assert reader.read() == []
        assert reader.read() == [b"new\n"]
    finally:
        reader.close()


def test_webhook_retry_redaction_bounding_and_recovery(monkeypatch, caplog):
    """外部投递失败会限频重试，异常 URL/响应正文不进入日志，恢复后清空队列。"""
    calls = []

    def handle(request):
        """用内存 HTTP 端点模拟失败后恢复，不联系真实通知服务。"""
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, text="synthetic-secret-response")

    original_client = httpx.Client

    def client(**kwargs):
        """仅替换传输层，保留生产超时与重定向选项。"""
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(cookiecloud.httpx, "Client", client)
    with caplog.at_level(logging.WARNING):
        sink = Alerts("https://alerts.test/synthetic-secret-token")
        sink.add({"event": "cookiecloud_security_alert", "scope": "global", "reason": "missing_keys"})
        sink.flush(6000)
        assert len(sink.pending) == len(calls) == 1
        sink.flush(6059)
        assert len(calls) == 1
        sink.flush(6060)
        assert len(calls) == 2 and not sink.pending
        assert json.loads(calls[-1].content)["alerts"][0]["reason"] == "missing_keys"
        assert "synthetic-secret" not in caplog.text
        for _ in range(105):
            sink.add({"event": "cookiecloud_security_alert"})
        assert len(sink.pending) == 100 and sink.dropped == 5


def test_webhook_redirect_is_not_followed(monkeypatch):
    """重定向目标无法接收到原 Webhook 告警或部署凭据。"""
    calls = []

    def handle(request):
        """拒绝任何意外的第二次网络请求。"""
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.test/"})

    original_client = httpx.Client

    def client(**kwargs):
        """将所有网络操作限制在本地内存传输中。"""
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(cookiecloud.httpx, "Client", client)
    sink = Alerts("https://alerts.test/private")
    sink.add({"event": "cookiecloud_security_alert"})
    sink.flush(6000)
    assert len(calls) == 1 and len(sink.pending) == 1


def test_monitor_cli_delivers_real_webhook_without_url_or_payload_leaks(tmp_path):
    """真实启动进程并投递本机 Webhook，确认 INFO 客户端日志也不会泄露私有 URL。"""
    received = []

    class Webhook(BaseHTTPRequestHandler):
        """仅在本机接收隔离测试告警，并模拟通知平台临时失败。"""

        def do_POST(self):
            """读取告警 JSON 后返回含敏感占位内容的错误正文。"""
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"synthetic-secret-response")

        def log_message(self, *_):
            """禁止 HTTP 测试服务打印私有 Webhook 路径。"""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Webhook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log = tmp_path / "security.log"
    log.write_bytes(b"\n".join(event(time.time(), status=429, path="synthetic-secret-key") for _ in range(20)) + b"\n")
    environment = {**os.environ, "COOKIECLOUD_ALERT_WEBHOOK": f"http://127.0.0.1:{server.server_port}/synthetic-secret-url"}
    try:
        result = subprocess.run([sys.executable, "-m", "app.services.cookiecloud", "--once", "--log", str(log)],
                                cwd=Path(__file__).parents[1], env=environment, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert received[0]["alerts"][0]["reason"] == "rate_limited"
        assert "webhook failed" in result.stderr
        assert "synthetic-secret" not in result.stdout + result.stderr
        missing = subprocess.run([sys.executable, "-m", "app.services.cookiecloud", "--once", "--log", str(tmp_path / "missing")],
                                 cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10)
        assert missing.returncode == 1 and "log unavailable" in missing.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
