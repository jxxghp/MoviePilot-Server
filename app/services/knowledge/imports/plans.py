"""
JSONL 流式规范化与不可变预检计划；不执行正文、字段映射或包内指令。
"""

import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
from collections import Counter

from sqlalchemy import select

from app.models.knowledge import KnowledgeDocument, KnowledgeImportReceipt, KnowledgeRevision, new_identity
from app.schemas.knowledge import ImportPreflight, RevisionPayload
from app.services.knowledge.imports import files
from app.services.knowledge.checks import content_flags
from app.services.knowledge.documents import revision_values

SCOPE_KEYS = {"product", "version_scope", "min_version", "max_version", "source_commit", "fetched_at", "valid_until"}
ROW_FIELDS = ("line", "external_id", "title", "document_id", "base_version", "base_revision", "action", "flags")


class PlanBlocked(ValueError):
    """
    使用固定代码报告数据问题，不能把 JSON、正文或凭据放进异常响应。
    """

    def __init__(self, code: str):
        """
        保存内部白名单调用点给出的代码，而不接受原文异常作展示信息。
        """
        super().__init__(code)
        self.code = code


def encoded(value) -> str:
    """
    用稳定序列化计算内容和计划身份，保留 Markdown 与 Unicode 正文。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(values: dict) -> str:
    """
    正文由 content_hash 覆盖，标题、出处、时间及版本范围的改变也属于修订改变。
    """
    return hashlib.sha256(encoded({key: value for key, value in values.items() if key != "content"}).encode()).hexdigest()


def distinct_pairs(pairs: list) -> dict:
    """
    拒绝重复 JSON 键，不让同一字段的后值静默覆盖前值。
    """
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def invalid_constant(_value):
    """
    拒绝 JSON 标准之外的 NaN 与 Infinity。
    """
    raise ValueError("invalid_json_constant")


def normalize(raw: dict, mapping: ImportPreflight, source, collection) -> tuple:
    """
    根据明确映射规范化一行；外部来源筛选与中心集合权限相互独立。
    """
    if mapping.origin_field:
        if raw.get(mapping.origin_field) not in mapping.origin_values:
            return "", None, {"filtered_origin"}, "excluded"
    flags = content_flags(encoded(raw))
    if "suspected_secret" in flags:
        raise PlanBlocked("suspected_secret")
    if "source_id" in raw and mapping.origin_field != "source_id":
        raise PlanBlocked("unmapped_origin")
    bindings = {"domain": collection.domain, "collection_id": collection.id, "site_id": source.site_id}
    if any(key in raw and raw[key] != value for key, value in bindings.items()):
        raise PlanBlocked("scope_mismatch")
    identity = [raw.get(field) for field in mapping.identity_fields]
    if any(not isinstance(item, str) or not item.strip() or len(item) > 512
           or any(ord(char) < 32 for char in item) for item in identity):
        raise PlanBlocked("invalid_identity")
    external = identity[0] if len(identity) == 1 else "jsonl:" + hashlib.sha256(encoded(identity).encode()).hexdigest()
    title, content = raw.get(mapping.title_field), raw.get(mapping.content_field)
    if not isinstance(title, str) or not isinstance(content, str):
        raise PlanBlocked("invalid_record")
    flags |= content_flags(title + "\n" + content + "\n" + external)
    if "suspected_secret" in flags:
        raise PlanBlocked("suspected_secret")
    base = {**mapping.metadata, "title": title, "content": content,
            "source_url": raw.get(mapping.url_field, "") if mapping.url_field else ""}
    record_meta = {key: raw[key] for key in SCOPE_KEYS if key in raw}
    try:
        fixed = revision_values(RevisionPayload(**base), collection.product)
        values = revision_values(RevisionPayload(**{**base, **record_meta}), collection.product)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise PlanBlocked("invalid_record") from error
    if mapping.metadata_mode == "fixed" and fingerprint(fixed) != fingerprint(values):
        raise PlanBlocked("scope_mismatch")
    if collection.product and values["product"] != collection.product:
        raise PlanBlocked("scope_mismatch")
    if values["version_scope"] == "range" and not values["product"]:
        raise PlanBlocked("scope_mismatch")
    if values["version_scope"] == "unknown":
        flags.add("scope_unknown")
    used = {*mapping.identity_fields, mapping.title_field, mapping.content_field, mapping.url_field,
            mapping.origin_field, *SCOPE_KEYS, *bindings}
    if set(raw) - used:
        flags.add("unmapped_fields")
    return external, values, flags, "new"


def open_plan(directory, batch):
    """
    打开中心生成的只读计划，并核对格式与批次绑定，不打开客户端提交的 SQLite。
    """
    location = files.path(directory, batch.id, batch.plan_id)
    if location.is_symlink():
        raise ValueError("invalid_plan")
    db = sqlite3.connect(location.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    try:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata != {"format": "1", "batch": batch.id, "complete": "1"}:
            raise ValueError("invalid_plan")
    except BaseException:
        db.close()
        raise
    return db


def row_data(row) -> dict:
    """
    列表只展示可复核的元数据，正文需要经过单行详情入口。
    """
    return {**{key: row[key] for key in ROW_FIELDS if key != "flags"}, "flags": json.loads(row["flags"])}


async def build_plan(sessions, directory, batch, mapping: ImportPreflight, source, collection) -> tuple:
    """
    有界流式读取并批量比对当前头修订，写出私有计划后由租约事务决定是否接纳。
    """
    identity = new_identity()
    temporary = directory / (batch.id + "-building-" + identity + ".sqlite")
    destination = files.path(directory, batch.id, identity)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(descriptor)
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""
            PRAGMA temp_store=MEMORY;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE rows (
                line INTEGER PRIMARY KEY, external_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                document_id TEXT, base_version INTEGER, base_revision INTEGER, action TEXT NOT NULL, flags TEXT NOT NULL,
                payload TEXT, payload_hash TEXT, content_hash TEXT
            );
            CREATE INDEX by_identity ON rows(external_id);
            CREATE INDEX by_content ON rows(content_hash);
        """)
        digest = hashlib.sha256()
        pending = []

        async def flush():
            """
            一次只加载五十篇当前修订，防止逐行 SQL 和全包内存膨胀。
            """
            if not pending:
                return
            async with sessions() as session:
                existing = (await session.execute(select(KnowledgeDocument, KnowledgeRevision).join(
                    KnowledgeRevision, (KnowledgeRevision.document_id == KnowledgeDocument.id) &
                    (KnowledgeRevision.number == KnowledgeDocument.head_revision),
                ).where(KnowledgeDocument.source_id == batch.source_id,
                        KnowledgeDocument.external_id.in_([item[1] for item in pending])))).all()
                imported = set((await session.scalars(select(KnowledgeDocument.id).join(
                    KnowledgeImportReceipt, (KnowledgeImportReceipt.document_id == KnowledgeDocument.id) &
                    (KnowledgeImportReceipt.revision_number == KnowledgeDocument.head_revision),
                ).where(KnowledgeDocument.source_id == batch.source_id,
                        KnowledgeDocument.external_id.in_([item[1] for item in pending]),
                        KnowledgeImportReceipt.outcome.in_(["new", "updated"])).distinct())).all())
            current = {document.external_id: (document, revision) for document, revision in existing}
            for number, external, values, flags in pending:
                body_hash = values["content_hash"]
                payload_hash = fingerprint(values)
                document, revision = current.get(external, (None, None))
                action = "new"
                if document:
                    previous = {key: getattr(revision, key) for key in values}
                    action = "unchanged" if fingerprint(previous) == payload_hash else "updated"
                    if action == "updated" and document.id not in imported:
                        action = "conflict"
                        flags.add("manual_revision")
                duplicate = db.execute("SELECT line FROM rows WHERE external_id=? LIMIT 1", (external,)).fetchone()
                if duplicate:
                    action = "blocked"
                    flags.add("duplicate_identity")
                    for earlier in db.execute("SELECT line,flags FROM rows WHERE external_id=?", (external,)).fetchall():
                        earlier_flags = set(json.loads(earlier[1])) | {"duplicate_identity"}
                        db.execute("UPDATE rows SET action='blocked', flags=?,payload=NULL WHERE line=?",
                                   (encoded(sorted(earlier_flags)), earlier[0]))
                if db.execute("SELECT 1 FROM rows WHERE content_hash=? LIMIT 1", (body_hash,)).fetchone():
                    flags.add("duplicate_content")
                db.execute("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    number, external, values["title"] if action != "blocked" else "",
                    document.id if document else new_identity(), document.version if document else None,
                    document.head_revision if document else None, action, encoded(sorted(flags)),
                    encoded(values) if action != "blocked" else None, payload_hash, body_hash,
                ))
            pending.clear()

        with files.read(files.path(directory, batch.id)) as stream:
            number = 0
            while line := stream.readline(files.MAX_LINE + 1):
                number += 1
                digest.update(line)
                if len(line) > files.MAX_LINE:
                    raise PlanBlocked("line_too_large")
                if number > files.MAX_ROWS:
                    raise PlanBlocked("too_many_rows")
                try:
                    raw = json.loads(line.decode("utf-8"), object_pairs_hook=distinct_pairs,
                                     parse_constant=invalid_constant)
                    if not isinstance(raw, dict):
                        raise PlanBlocked("invalid_record")
                    external, values, flags, action = normalize(raw, mapping, source, collection)
                    if action == "excluded":
                        db.execute("INSERT INTO rows(line,action,flags) VALUES (?,?,?)",
                                   (number, action, encoded(sorted(flags))))
                    else:
                        pending.append((number, external, values, flags))
                except (ValueError, TypeError, UnicodeError, RecursionError) as error:
                    code = error.code if isinstance(error, PlanBlocked) else "invalid_json"
                    db.execute("INSERT INTO rows(line,action,flags) VALUES (?,?,?)",
                               (number, "blocked", encoded([code])))
                if len(pending) >= 50:
                    await flush()
                await asyncio.sleep(0)
        await flush()
        if digest.hexdigest() != batch.file_hash:
            raise PlanBlocked("file_changed")
        summary = {"rows": number, "actions": {}, "flags": {}}
        summary["actions"] = dict(db.execute("SELECT action,count(*) FROM rows GROUP BY action"))
        counts = Counter()
        for row in db.execute("SELECT flags FROM rows"):
            counts.update(json.loads(row[0]))
        summary["flags"] = dict(counts)
        if number == 0 or summary["actions"].get("excluded", 0) == number:
            raise PlanBlocked("empty_selection")
        db.executemany("INSERT INTO metadata VALUES (?,?)", [("format", "1"), ("batch", batch.id), ("complete", "1")])
        db.commit()
        db.close()
        if temporary.stat().st_size > files.MAX_BYTES * 2:
            raise PlanBlocked("plan_too_large")
        os.chmod(temporary, 0o400)
        with files.read(temporary) as stream:
            os.fsync(stream.fileno())
        temporary.rename(destination)
        return identity, files.digest(destination), summary
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            db.close()
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        temporary.with_name(temporary.name + "-journal").unlink(missing_ok=True)
        raise
