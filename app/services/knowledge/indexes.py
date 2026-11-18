"""
中心私有的不可变 SQLite FTS5 索引；构建只读取已发布且启用的知识。
"""

import asyncio
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from sqlalchemy import or_, select

from app.models.knowledge import (
    KnowledgeCollection, KnowledgeDocument, KnowledgeRevision, KnowledgeSite, KnowledgeSource, new_identity, timestamp,
)

FORMAT_VERSION = 1
CHUNK_SIZE = 1800
CHUNK_OVERLAP = 180


class FtsUnavailable(RuntimeError):
    """
    部署环境缺少 FTS5 时明确失败，不生成外观成功但无法检索的文件。
    """


def private_directory(configured: str) -> Path:
    """
    要求明确指定仓库外专用私有目录，防止正文落入源码、镜像上下文或共享临时目录。
    """
    path = Path(configured)
    if not configured or not path.is_absolute():
        raise ValueError("请设置 KNOWLEDGE_DATA_DIR 为仓库外的绝对私有目录")
    path = path.resolve()
    source_root = Path(__file__).resolve().parents[3]
    if len(path.parts) < 3 or path.is_relative_to(source_root):
        raise ValueError("知识私有目录不能位于源码目录或文件系统根目录")
    if path.is_relative_to("/tmp") or path.is_relative_to("/private/tmp"):
        raise ValueError("知识私有目录不能使用共享临时目录")
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("知识私有目录不能位于 Git 仓库内")
    if path.exists() and (not path.is_dir() or path.stat().st_mode & 0o077):
        raise ValueError("已有知识私有目录必须为仅属主可访问的目录")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    indexes = path / "indexes"
    if indexes.is_symlink():
        raise ValueError("索引子目录不能是符号链接")
    indexes.mkdir(mode=0o700, exist_ok=True)
    if indexes.stat().st_mode & 0o077:
        raise ValueError("索引子目录必须仅属主可访问")
    return indexes


def tokens(text: str) -> str:
    """
    显式生成中文单字及相邻双字、英文和完整配置名；不依赖环境中可变的分词词典。
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    result = re.findall(r"[a-z0-9_]+", normalized)
    for phrase in re.findall(r"[\u3400-\u9fff]+", normalized):
        result.extend(phrase)
        result.extend(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return " ".join(result)


def chunks(content: str):
    """
    按段落附近切分并保留字符偏移及少量重叠，长代码行也有明确大小上限。
    """
    start = 0
    while start < len(content):
        end = min(start + CHUNK_SIZE, len(content))
        if end < len(content):
            boundary = content.rfind("\n", start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        segment = content[start:end]
        left = len(segment) - len(segment.lstrip())
        right = len(segment.rstrip())
        if right > left:
            yield start + left, start + right, content[start + left:start + right]
        if end == len(content):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)


class SnapshotBuilder:
    """
    在私有构建文件内完成所有写入，关闭和校验成功后才产生不可变快照。
    """

    def __init__(self, directory: Path, generation: int):
        """
        排他创建 0600 构建文件；不接受 API 传入的路径或文件名。
        """
        self.snapshot = new_identity()
        self.directory = directory
        self.temporary = directory / (".building-" + self.snapshot + ".sqlite")
        self.destination = directory / (self.snapshot + ".sqlite")
        self.connection = None
        self.documents = 0
        self.chunk_count = 0
        descriptor = os.open(self.temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.executescript("""
                PRAGMA journal_mode=DELETE;
                PRAGMA temp_store=MEMORY;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, revision_number INTEGER NOT NULL,
                    chunk_number INTEGER NOT NULL, collection_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    site_id TEXT, title TEXT NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
                    content TEXT NOT NULL, UNIQUE(document_id, revision_number, chunk_number)
                );
            """)
            try:
                self.connection.execute("""
                    CREATE VIRTUAL TABLE search USING fts5(
                        title, body, content='', tokenize='unicode61 remove_diacritics 2 tokenchars ''_'''
                    )
                """)
            except sqlite3.OperationalError as error:
                if "no such module: fts5" in str(error).lower():
                    raise FtsUnavailable() from error
                raise
            self.connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
                ("format_version", str(FORMAT_VERSION)), ("generation", str(generation)), ("created_at", timestamp()),
            ])
        except BaseException:
            self.abort()
            raise

    def add(self, row) -> None:
        """
        写入一个发布修订及其可回溯片段，全文只留在中心私有文件中。
        """
        for number, (start, end, content) in enumerate(chunks(row.content)):
            result = self.connection.execute(
                "INSERT INTO chunks (document_id, revision_number, chunk_number, collection_id, source_id, site_id, "
                "title, start_offset, end_offset, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row.document_id, row.revision_number, number, row.collection_id, row.source_id, row.site_id,
                 row.title, start, end, content),
            )
            self.connection.execute("INSERT INTO search(rowid, title, body) VALUES (?, ?, ?)",
                                    (result.lastrowid, tokens(row.title), tokens(content)))
            self.chunk_count += 1
        self.documents += 1

    def finish(self) -> None:
        """
        校验完整文件并原子改名；只有此后才允许 SQL 事务激活其标识。
        """
        self.connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
            ("documents", str(self.documents)), ("chunks", str(self.chunk_count)), ("complete", "1"),
        ])
        self.connection.commit()
        if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("snapshot_integrity")
        self.connection.close()
        self.connection = None
        os.chmod(self.temporary, 0o400)
        with self.temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        self.temporary.rename(self.destination)
        directory = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def abort(self) -> None:
        """
        取消或失败时关闭连接并清理本次临时文件，不碰其他任务或在线快照。
        """
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.temporary.unlink(missing_ok=True)
        Path(str(self.temporary) + "-journal").unlink(missing_ok=True)


async def build_snapshot(sessions, directory: Path, generation: int) -> SnapshotBuilder:
    """
    使用有界主键分页读取 SQL，每篇让出调度机会，使独立 worker 心跳和取消能运行。
    """
    builder = SnapshotBuilder(directory, generation)
    after = ""
    try:
        while True:
            async with sessions() as db:
                rows = (await db.execute(select(
                    KnowledgeDocument.id.label("document_id"), KnowledgeRevision.number.label("revision_number"),
                    KnowledgeSource.collection_id, KnowledgeSource.id.label("source_id"), KnowledgeSource.site_id,
                    KnowledgeRevision.title, KnowledgeRevision.content,
                ).join(KnowledgeRevision, (KnowledgeRevision.document_id == KnowledgeDocument.id) &
                       (KnowledgeRevision.number == KnowledgeDocument.published_revision))
                    .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
                    .join(KnowledgeCollection, KnowledgeCollection.id == KnowledgeSource.collection_id)
                    .outerjoin(KnowledgeSite, KnowledgeSite.id == KnowledgeSource.site_id)
                    .where(KnowledgeDocument.id > after, KnowledgeRevision.state == "published",
                           KnowledgeSource.enabled.is_(True), KnowledgeCollection.enabled.is_(True),
                           or_(KnowledgeSource.site_id.is_(None), KnowledgeSite.enabled.is_(True)))
                    .order_by(KnowledgeDocument.id).limit(50))).all()
            if not rows:
                break
            for row in rows:
                builder.add(row)
                await asyncio.sleep(0)
            after = rows[-1].document_id
        builder.finish()
        return builder
    except BaseException:
        builder.abort()
        # finish() 改名后的任何失败也只删除本次尚未激活的文件。
        builder.destination.unlink(missing_ok=True)
        raise
