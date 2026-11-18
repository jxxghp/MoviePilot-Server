"""
知识导入的中心私有文件与限额；文件身份由中心生成，不能由请求提供路径。
"""

import hashlib
import os
import re
import shutil
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_ROWS = 50000
MAX_STAGING = 512 * 1024 * 1024
MAX_BATCHES = 4
STAGING_SECONDS = 86400


def import_directory(index_directory: Path) -> Path:
    """
    复用已校验私有根目录，在受限子目录暂存原文件与不可变计划。
    """
    directory = index_directory.parent / "imports"
    if directory.is_symlink():
        raise ValueError("导入目录不能是符号链接")
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError("导入目录必须仅属主可访问")
    return directory


def path(directory: Path, batch: str, plan: str = "") -> Path:
    """
    只接受中心生成的固定身份，不将外部文档 ID 拼入路径。
    """
    if not re.fullmatch(r"[a-f0-9]{32}", batch) or (plan and not re.fullmatch(r"[a-f0-9]{32}", plan)):
        raise ValueError("invalid_private_identity")
    return directory / (batch + ("-" + plan + ".sqlite" if plan else ".jsonl"))


def read(path: Path):
    """
    打开私有普通文件时拒绝符号链接，调用者用上下文及时关闭。
    """
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    return os.fdopen(descriptor, "rb")


def digest(path: Path) -> str:
    """
    分块校验文件完整性，不将整个知识包读入内存。
    """
    result = hashlib.sha256()
    with read(path) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_quota(directory: Path, *, reserve: int | None = None) -> None:
    """
    每次创建前为原文件或计划检查暂存准入和磁盘余量，避免无界增长。
    """
    size = sum(entry.stat(follow_symlinks=False).st_size for entry in os.scandir(directory)
               if entry.is_file(follow_symlinks=False))
    reserve = MAX_BYTES * 2 if reserve is None else reserve
    if size + reserve > MAX_STAGING or shutil.disk_usage(directory).free < MAX_BYTES * 4:
        raise OSError("staging_quota")


def remove_completed_plans(directory: Path, batch: str) -> None:
    """
    新预检已使旧计划失效后清理已完成文件，不触碰尚在构建的旧租约文件。
    """
    path(directory, batch)
    pattern = re.compile(re.escape(batch) + r"-[a-f0-9]{32}\.sqlite")
    for entry in directory.iterdir():
        if pattern.fullmatch(entry.name) and not entry.is_dir():
            entry.unlink(missing_ok=True)


def remove_batch(directory: Path, batch: str) -> None:
    """
    只清理属于明确批次的暂存和 SQLite 日志，不递归删除目录或触碰其他产物。
    """
    path(directory, batch)
    pattern = re.compile(re.escape(batch) + r"(?:-(?:building-|uploading-)?[a-f0-9]{32})?\.(?:jsonl|sqlite)(?:-journal)?")
    for entry in directory.iterdir():
        if pattern.fullmatch(entry.name) and not entry.is_dir():
            entry.unlink(missing_ok=True)
