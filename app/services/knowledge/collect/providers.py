"""
固定 GitHub 快照与批准网页的采集，所有网络请求都经过来源绑定的受控传输。
"""

import asyncio
import base64
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from app.models.knowledge import timestamp
from app.services.knowledge.collect import parsing
from app.services.knowledge.network import client
from app.services.knowledge.network.policy import NetworkError, source_scope

MAX_FILES = 100
MAX_DIRECTORIES = 40
MAX_TOTAL_BYTES = 8 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".mdx", ".txt", ".rst", ".py", ".rs", ".ts", ".js", ".vue", ".json", ".yaml",
                 ".yml", ".toml", ".ini", ".conf", ".sh", ".cfg", ".sql", ".html", ".css"}


@dataclass
class Page:
    """单项结果在内存中交给事务层，调试表示不含归一正文。"""

    external_id: str
    values: dict | None = field(default=None, repr=False)
    flags: list[str] = field(default_factory=list)
    commit: str = ""
    excluded: bool = False
    retry_after: int | None = None


def parsed_json(result):
    """GitHub 返回严格对象或数组，拒绝 HTML、304 和异常大小后才由连接器使用。"""
    try:
        if result.status != 200 or result.content_type not in {"application/json", "application/vnd.github+json"}:
            raise ValueError("response_type")
        return json.loads(result.body)
    except (ValueError, TypeError):
        raise NetworkError("github_response_invalid") from None


async def github(source, auth):
    """固定 commit / tree / blob；检查真实文件 mode，不随 Contents API 隐式跟随符号链接。"""
    scope = source_scope(source)
    base = "https://api.github.com/repos/" + source.repository
    metadata = parsed_json(await client.fetch(scope, base + "/commits/" + quote(source.branch, safe=""), auth))
    commit = metadata.get("sha", "") if isinstance(metadata, dict) else ""
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
        raise NetworkError("github_commit_invalid")
    detail = metadata.get("commit")
    tree_info = detail.get("tree") if isinstance(detail, dict) else None
    root = tree_info.get("sha", "") if isinstance(tree_info, dict) else ""
    trees = {}

    async def tree(identity):
        """非递归读取快照中的目录，截断或过大明确失败，不静默只收前若干文件。"""
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", identity):
            raise NetworkError("github_response_invalid")
        if identity not in trees:
            if len(trees) >= MAX_DIRECTORIES:
                raise NetworkError("source_scope_too_large")
            data = parsed_json(await client.fetch(scope, base + "/git/trees/" + identity, auth))
            if not isinstance(data, dict) or data.get("sha") != identity or not isinstance(data.get("tree"), list):
                raise NetworkError("github_response_invalid")
            if data.get("truncated") or len(data["tree"]) >= 1000:
                raise NetworkError("source_scope_too_large")
            entries = {}
            for item in data["tree"]:
                name = item.get("path") if isinstance(item, dict) else None
                if (not isinstance(name, str) or name in {"", ".", ".."} or "/" in name or "\\" in name
                        or len(name) > 512 or any(ord(char) < 32 for char in name) or name in entries):
                    raise NetworkError("github_response_invalid")
                entries[name] = item
            trees[identity] = entries
        return trees[identity]

    pending, pages = [], []
    for path in dict.fromkeys(json.loads(source.paths)):
        identity, item = root, None
        parts = path.rstrip("/").split("/")
        try:
            for index, part in enumerate(parts):
                item = (await tree(identity)).get(part)
                if not item:
                    raise NetworkError("github_path_missing")
                if index < len(parts) - 1:
                    if item.get("mode") != "040000" or item.get("type") != "tree":
                        raise NetworkError("github_file_unsupported")
                    identity = item["sha"]
            pending.append((path.rstrip("/"), item))
        except NetworkError as error:
            if error.code == "source_scope_too_large":
                raise
            pages.append(Page(path, flags=[error.code], commit=commit, retry_after=error.retry_after))
    seen, files, total = set(), 0, 0
    while pending:
        path, item = pending.pop(0)
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > 1000 or len(path) > 512:
            raise NetworkError("source_scope_too_large")
        try:
            if item.get("mode") == "040000" and item.get("type") == "tree":
                children = await tree(item["sha"])
                pending.extend((path + "/" + name, children[name]) for name in sorted(children))
                continue
            if (item.get("mode") not in {"100644", "100755"} or item.get("type") != "blob"
                    or (posixpath.splitext(path)[1] and posixpath.splitext(path)[1].lower() not in TEXT_SUFFIXES)):
                pages.append(Page(path, flags=["file_type_excluded"], commit=commit, excluded=True))
                continue
            files += 1
            if files > MAX_FILES:
                raise NetworkError("source_scope_too_large")
            blob = item.get("sha", "")
            if not isinstance(blob, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", blob):
                raise NetworkError("github_response_invalid")
            data = parsed_json(await client.fetch(scope, base + "/git/blobs/" + blob, auth))
            if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
                raise NetworkError("github_response_invalid")
            body = base64.b64decode(data["content"].replace("\n", ""), validate=True)
            hasher = hashlib.sha1 if len(blob) == 40 else hashlib.sha256
            digest = hasher(b"blob " + str(len(body)).encode() + b"\x00" + body).hexdigest()
            if len(body) != data.get("size") or digest != data.get("sha") or digest != blob:
                raise NetworkError("github_integrity_failed")
            total += len(body)
            if total > MAX_TOTAL_BYTES:
                raise NetworkError("source_scope_too_large")
            text = parsing.decode(body)
            flags = parsing.check_text(path + "\n" + text, auth)
            pages.append(Page(path, {"title": path[:300], "content": text, "source_commit": commit,
                                    "source_url": "https://github.com/" + source.repository + "/blob/" + commit + "/" + quote(path),
                                    "fetched_at": timestamp()}, flags, commit))
        except NetworkError as error:
            if error.code == "source_scope_too_large":
                raise
            pages.append(Page(path, flags=[error.code], commit=commit, retry_after=error.retry_after))
        except (ValueError, TypeError, UnicodeError):
            pages.append(Page(path, flags=["github_response_invalid"], commit=commit))
    return pages or [Page("source", flags=["document_empty"], commit=commit)]


async def webpages(source, auth):
    """显式网页互不跟链；逐页失败可见，不把某页异常伪装成整批成功。"""
    scope = source_scope(source)
    pages, total, normalized = [], 0, 0
    if len(scope.pages) > 101:
        raise NetworkError("source_scope_too_large")
    for url in scope.pages:
        identity = "page:" + hashlib.sha256(url.encode()).hexdigest()
        try:
            response = await client.fetch(scope, url, auth)
            total += len(response.body)
            if total > MAX_TOTAL_BYTES:
                raise NetworkError("source_scope_too_large")
            result = await asyncio.to_thread(parsing.document, response, source, source.name, auth=auth)
            normalized += len(result["content"].encode())
            if normalized > MAX_TOTAL_BYTES:
                raise NetworkError("source_scope_too_large")
            flags = result.pop("flags")
            pages.append(Page(identity, {**result, "source_url": response.url, "fetched_at": timestamp()}, flags))
        except NetworkError as error:
            if error.code == "source_scope_too_large":
                raise
            pages.append(Page(identity, flags=[error.code], retry_after=error.retry_after))
    return pages


async def gather(source, auth):
    """执行器只分派内置连接器，不执行来源中的 Python、脚本或任意函数名。"""
    return await (github(source, auth) if source.connector == "github" else webpages(source, auth))
