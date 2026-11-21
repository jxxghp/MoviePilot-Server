"""独立监视 CookieCloud 脱敏代理日志，不读取 CookieCloud 数据或加载业务数据库。"""

import argparse
from collections import OrderedDict
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time

import httpx

logger = logging.getLogger(__name__)
WINDOW = 300
COOLDOWN = 900
MAX_CLIENTS = 10000
MAX_PENDING = 100
# 分别检测不存在的 KEY、超限和服务故障；全局阈值覆盖代理池分散扫描。
CLIENT_THRESHOLDS = {"missing_keys": 20, "rate_limited": 20}
GLOBAL_THRESHOLDS = {"missing_keys": 100, "rate_limited": 100, "upstream_errors": 10}


class Window:
    """以分钟桶限制五分钟计数的内存占用，并为每种告警维护独立冷却。"""

    def __init__(self):
        """初始化有界时间桶和最多三项告警冷却记录。"""
        self.buckets = {}
        self.alerted = {}

    def record(self, stamp: float, reason: str, now: float, thresholds: dict) -> list[tuple[str, int]]:
        """累计最近五个分钟桶，跨过阈值时返回告警并抑制十五分钟内重复触发。"""
        minute = int(now // 60)
        self.buckets = {key: value for key, value in self.buckets.items() if key > minute - WINDOW // 60}
        bucket = int(stamp // 60)
        if bucket <= minute - WINDOW // 60:
            return []
        counts = self.buckets.setdefault(bucket, {})
        counts[reason] = counts.get(reason, 0) + 1
        total = sum(value.get(reason, 0) for value in self.buckets.values())
        if reason not in thresholds or total < thresholds[reason] or now - self.alerted.get(reason, -COOLDOWN) < COOLDOWN:
            return []
        self.alerted[reason] = now
        return [(reason, total)]


class Monitor:
    """校验日志字段后按真实 IP 和全局计数，丢弃任何非约定字段。"""

    def __init__(self):
        """限制来源跟踪数量，避免扫描本身耗尽监控内存。"""
        self.clients = OrderedDict()
        self.global_window = Window()

    def observe(self, line: bytes, now: float) -> list[dict]:
        """只接受近期有效记录，输出不含路径、KEY、密码或正文的固定结构告警。"""
        try:
            entry = json.loads(line)
            stamp = float(entry["time"])
            address = str(ipaddress.ip_address(entry["ip"]))
            status = int(entry["status"])
            operation = entry["operation"]
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return []
        if not now - WINDOW <= stamp <= now + 5:
            return []
        reason = ("rate_limited" if status == 429 else
                  "missing_keys" if status == 404 and operation == "pull" else
                  "upstream_errors" if 500 <= status <= 599 else None)
        if reason is None:
            return []
        client = self.clients.pop(address, None) or Window()
        self.clients[address] = client
        if len(self.clients) > MAX_CLIENTS:
            self.clients.popitem(last=False)
        alerts = []
        for scope, window, thresholds in ((address, client, CLIENT_THRESHOLDS),
                                           ("global", self.global_window, GLOBAL_THRESHOLDS)):
            for kind, count in window.record(stamp, reason, now, thresholds):
                alerts.append({"event": "cookiecloud_security_alert", "scope": scope,
                               "reason": kind, "count": count, "window_seconds": WINDOW,
                               "time": int(now)})
        return alerts


class Alerts:
    """始终输出脱敏 WARNING，可选批量投递 Webhook，失败后保留有界队列重试。"""

    def __init__(self, webhook: str):
        """Webhook 仅从部署环境读取，日志不输出 URL 或响应正文。"""
        self.webhook = webhook
        self.pending = []
        self.dropped = 0
        self.next_send = 0

    def add(self, alert: dict) -> None:
        """每项告警先写入容器日志，外部投递队列最多保留一百项。"""
        logger.warning("%s", json.dumps(alert, separators=(",", ":")))
        if self.webhook:
            if len(self.pending) < MAX_PENDING:
                self.pending.append(alert)
            else:
                self.dropped += 1

    def flush(self, now: float) -> None:
        """最多每分钟投递一批；不跟随重定向，失败后一分钟重试且不泄露异常载荷。"""
        if not self.pending or now < self.next_send:
            return
        self.next_send = now + 60
        try:
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", self.webhook, json={"alerts": self.pending, "dropped": self.dropped}) as response:
                    response.raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            logger.error("CookieCloud alert webhook failed; retrying in 60 seconds")
            return
        self.pending.clear()
        self.dropped = 0


class LogReader:
    """增量读取 JSON 行，支持 rename 与 copytruncate 轮转，不输出原始错误行。"""

    def __init__(self, path: Path):
        """初次从头读取，使重启仍能检测当前窗口；过期日志由 Monitor 丢弃。"""
        self.path = path
        self.stream = None

    def close(self) -> None:
        """关闭持有的日志描述符，允许轮转文件及时回收。"""
        if self.stream:
            self.stream.close()
            self.stream = None

    def read(self, limit: int = 5000) -> list[bytes]:
        """每轮有界读取，保留未写完的行；先排空旧 inode，再打开轮转后的文件。"""
        if self.stream is None:
            self.stream = self.path.open("rb")
        if os.fstat(self.stream.fileno()).st_size < self.stream.tell():
            self.stream.seek(0)
        lines = []
        for _ in range(limit):
            position = self.stream.tell()
            line = self.stream.readline(4097)
            if not line:
                try:
                    current = self.path.stat()
                except FileNotFoundError:
                    # rename 后新日志尚未创建时，保留旧描述符与本轮已读取的记录。
                    break
                previous = os.fstat(self.stream.fileno())
                if (current.st_dev, current.st_ino) != (previous.st_dev, previous.st_ino):
                    self.close()
                break
            if not line.endswith(b"\n"):
                if len(line) < 4097:
                    self.stream.seek(position)
                    break
                # 不把超长行余下的片段误当作新的安全事件。
                while line and not line.endswith(b"\n"):
                    line = self.stream.readline(4097)
                continue
            lines.append(line)
        return lines


def main() -> None:
    """启动独立监控进程，缺失日志或 Webhook 故障时输出脱敏运维告警。"""
    parser = argparse.ArgumentParser(description="CookieCloud security log monitor")
    parser.add_argument("--log", type=Path, default=Path("/logs/cookiecloud-security.log"))
    parser.add_argument("--once", action="store_true", help="读取当前日志后退出，用于部署验证")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # HTTP 客户端的 INFO/DEBUG 会输出含私有 token 的完整 Webhook URL。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    stopped = threading.Event()

    def stop(_signal, _frame):
        """收到退出信号后停止读取，并释放日志描述符。"""
        stopped.set()

    for event in (signal.SIGTERM, signal.SIGINT):
        signal.signal(event, stop)
    reader, monitor = LogReader(args.log), Monitor()
    alerts = Alerts(os.environ.get("COOKIECLOUD_ALERT_WEBHOOK", ""))
    unavailable = False
    try:
        while not stopped.is_set():
            try:
                lines = reader.read()
            except OSError:
                lines = []
                reader.close()
                if not unavailable:
                    logger.error("CookieCloud security log unavailable; check nginx log mount and permissions")
                unavailable = True
            else:
                if unavailable:
                    logger.info("CookieCloud security log recovered")
                unavailable = False
            now = time.time()
            for line in lines:
                for alert in monitor.observe(line, now):
                    alerts.add(alert)
            alerts.flush(now)
            if args.once and len(lines) < 5000:
                if unavailable:
                    raise SystemExit(1)
                break
            stopped.wait(0.2 if lines else 2)
    finally:
        reader.close()


if __name__ == "__main__":
    main()
