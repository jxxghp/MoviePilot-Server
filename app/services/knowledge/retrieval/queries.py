"""
范围优先、实时 SQL 授权的检索与补读，不把历史快照当作权限或当前事实。
"""

import re
import unicodedata
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, case, exists, func, or_, select

from app.core.config import settings
from app.models.knowledge import (
    KnowledgeClient, KnowledgeCollection, KnowledgeDocument, KnowledgeGrant, KnowledgeRevision,
    KnowledgeSite, KnowledgeSiteName, KnowledgeSource, KnowledgeState, new_identity,
)
from app.schemas.knowledge import KnowledgeSearch, normalized_name
from app.services.knowledge import indexes
from app.services.knowledge.checks import content_flags
from app.services.knowledge.retrieval import session, storage

TOTAL_CHARACTERS = 6000
EXCERPT_CHARACTERS = 1600
SYNONYMS = {
    "分享率": ("ratio",), "ratio": ("分享率",), "hr": ("hitandrun", "做种"),
    "h&r": ("hr", "做种"), "hitandrun": ("hr", "做种"),
    "订阅": ("subscription", "subscribe"), "subscription": ("订阅",),
    "刮削": ("scrape",), "scrape": ("刮削",), "整理": ("transfer",), "transfer": ("整理",),
}
STOP_PHRASES = ("为什么", "怎么", "如何", "请问", "一下", "能不能", "帮我", "这个", "那个", "什么")
STOP_WORDS = {"what", "how", "why", "the", "is", "are", "to", "my", "a", "and", "or", "not", "near", "mp", "pt", "moviepilot"}


def expression(query: str) -> str:
    """
    只构造引用后的字面词项，用户输入不能成为 FTS 操作符或 SQL。
    """
    text = unicodedata.normalize("NFKC", query).casefold()
    additions = [word for key, words in SYNONYMS.items()
                 if (re.search(r"(?<![a-z0-9_])" + re.escape(key) + r"(?![a-z0-9_])", text)
                     if key.isascii() else key in text) for word in words]
    text += " " + " ".join(additions)
    for phrase in STOP_PHRASES:
        text = text.replace(phrase, " ")
    terms = [word for word in re.findall(r"[a-z0-9_]+", text) if word not in STOP_WORDS]
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend([phrase] if len(phrase) == 1 else
                     [phrase[index:index + 2] for index in range(len(phrase) - 1)])
    terms = list(dict.fromkeys(terms))[:24]
    return " OR ".join('"' + term + '"' for term in terms)


async def collections(db, principal) -> list:
    """
    授权不使用进程或共享缓存，版本变化会使本次旧主体失效。
    """
    rows = (await db.execute(select(KnowledgeCollection.id, KnowledgeCollection.domain, KnowledgeCollection.product)
                             .join(KnowledgeGrant, KnowledgeGrant.collection_id == KnowledgeCollection.id)
                             .join(KnowledgeClient, KnowledgeClient.id == KnowledgeGrant.client_id).where(
        KnowledgeClient.id == principal.client_id, KnowledgeClient.enabled.is_(True),
        KnowledgeClient.version == principal.version, KnowledgeCollection.enabled.is_(True),
    ))).all()
    if not rows:
        raise HTTPException(403, "knowledge_access_changed")
    return rows


async def resolve_site(db, spec, collection_ids) -> str | None:
    """
    只解析当前授权来源内的启用站点；未知和不可见站点给同一结果，不泄露站点名单。
    """
    if not spec.site:
        return None
    return await db.scalar(select(KnowledgeSite.id).join(
        KnowledgeSiteName, KnowledgeSiteName.site_id == KnowledgeSite.id,
    ).join(KnowledgeSource, KnowledgeSource.site_id == KnowledgeSite.id).where(
        KnowledgeSiteName.normalized == normalized_name(spec.site), KnowledgeSite.enabled.is_(True),
        KnowledgeSource.enabled.is_(True), KnowledgeSource.collection_id.in_(collection_ids),
    ).distinct())


def metadata_statement(principal, spec, site_id):
    """
    正文和出处投影共享实时授权、发布指针、产品、站点和启停条件。
    """
    permitted = exists(select(KnowledgeGrant.client_id).join(
        KnowledgeClient, KnowledgeClient.id == KnowledgeGrant.client_id,
    ).where(KnowledgeGrant.client_id == principal.client_id,
            KnowledgeGrant.collection_id == KnowledgeCollection.id, KnowledgeClient.enabled.is_(True),
            KnowledgeClient.version == principal.version)).correlate(KnowledgeCollection)
    return select(
        KnowledgeDocument.id.label("document_id"), KnowledgeDocument.external_id,
        KnowledgeRevision.number.label("revision"), KnowledgeRevision.title, KnowledgeRevision.content_hash,
        KnowledgeRevision.product, KnowledgeRevision.version_scope, KnowledgeRevision.min_version,
        KnowledgeRevision.max_version, KnowledgeRevision.source_url, KnowledgeRevision.source_commit,
        KnowledgeRevision.fetched_at, KnowledgeRevision.valid_until, KnowledgeSource.id.label("source_id"),
        KnowledgeSource.name.label("source_label"), KnowledgeSource.site_id, KnowledgeCollection.domain,
        KnowledgeCollection.name.label("collection_label"),
    ).select_from(KnowledgeDocument).join(
        KnowledgeRevision, (KnowledgeRevision.document_id == KnowledgeDocument.id) &
        (KnowledgeRevision.number == KnowledgeDocument.published_revision),
    ).join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id).join(
        KnowledgeCollection, KnowledgeCollection.id == KnowledgeSource.collection_id,
    ).outerjoin(KnowledgeSite, KnowledgeSite.id == KnowledgeSource.site_id).where(
        permitted, KnowledgeSource.collection_id.in_(principal.collection_ids),
        KnowledgeRevision.state == "published", KnowledgeCollection.enabled.is_(True),
        KnowledgeSource.enabled.is_(True), or_(KnowledgeSource.site_id.is_(None), KnowledgeSite.enabled.is_(True)),
        KnowledgeRevision.product.in_(["", spec.product]),
        or_(KnowledgeSource.site_id.is_(None), KnowledgeSource.site_id == site_id) if site_id else
        KnowledgeSource.site_id.is_(None),
        KnowledgeCollection.domain == spec.domain if spec.domain else True,
    )


def applicability(row, spec) -> str | None:
    """
    明确版本不适用或过期时不返回正文；未知范围保留未知而非冒充适用。
    """
    if row.valid_until and datetime.fromisoformat(row.valid_until) <= datetime.now(timezone.utc):
        return "knowledge_expired"
    if row.version_scope == "range":
        requested = indexes.version_key(spec.product_version)
        if requested is None:
            return "product_version_required"
        if ((row.min_version and requested < indexes.version_key(row.min_version))
                or (row.max_version and requested > indexes.version_key(row.max_version))):
            return "version_not_applicable"
    return None


def evidence(row, candidate, excerpt: str) -> dict:
    """
    只显示经过实时授权的出处与适用性，不将采集时间未知写成当前时间。
    """
    freshness = ("valid" if row.valid_until else ("dated" if row.fetched_at else "unknown"))
    return {
        "document_id": row.document_id, "revision": row.revision, "title": row.title, "excerpt": excerpt,
        "source_label": row.source_label, "collection_label": row.collection_label, "source_url": row.source_url,
        "source_commit": row.source_commit, "domain": row.domain, "product": row.product, "site_id": row.site_id,
        "applicable_versions": {"scope": row.version_scope, "min": row.min_version, "max": row.max_version},
        "fetched_at": row.fetched_at, "valid_until": row.valid_until, "freshness": freshness,
        "stale": False if row.valid_until else None,
        "kind": indexes.content_kind(row.external_id, row.source_url),
        "section": "chunk:" + str(candidate["chunk_number"] + 1),
    }


def response(status: str, generation: int | None, evidence_items=None, notices=()) -> dict:
    """
    固定协议输出将不存在、范围不适用和资料差异分开，知识只作本轮临时证据。
    """
    return {"protocol_version": 1, "request_id": new_identity(), "corpus_revision": generation,
            "status": status, "evidence": evidence_items or [], "notices": sorted(set(notices)),
            "retention": "ephemeral", "content_trust": "reference_only", "reference_seconds": session.REFERENCE_SECONDS}


async def metadata(db, principal, spec, site_id, candidates) -> dict:
    """
    一批最多验证有限候选，只选择已发布修订，列表查询不取正文。
    """
    ids = list(dict.fromkeys(item["document_id"] for item in candidates))
    if not ids:
        return {}
    rows = (await db.execute(metadata_statement(principal, spec, site_id).where(
        KnowledgeDocument.id.in_(ids),
    ))).all()
    return {(row.document_id, row.revision): row for row in rows}


async def search(db, principal, spec) -> dict:
    """
    通过范围过滤、有限候选、实时 SQL 再核对后签发少量去重证据。
    """
    if "suspected_secret" in content_flags(spec.query + "\n" + (spec.site or "")):
        raise HTTPException(422, "knowledge_query_contains_sensitive_data")
    granted = await collections(db, principal)
    allowed = [row.id for row in granted if (not spec.domain or row.domain == spec.domain)
               and row.product in {"", spec.product}]
    if not allowed:
        return response("not_applicable", None, notices=["scope_unavailable"])
    site_id = await resolve_site(db, spec, allowed)
    if spec.site and not site_id:
        return response("not_applicable", None, notices=["unknown_or_unavailable_site"])
    terms = expression(spec.query)
    if not terms:
        return response("no_match", None, notices=["query_too_general"])
    try:
        directory = indexes.private_directory(settings.KNOWLEDGE_DATA_DIR)
    except (ValueError, OSError):
        raise HTTPException(503, "knowledge_storage_unavailable") from None
    state = await db.get(KnowledgeState, "main", populate_existing=True)
    if not state or not state.active_snapshot:
        raise HTTPException(503, "knowledge_index_not_ready")
    snapshot = state.active_snapshot
    generation = state.active_generation
    notices = ["site_unspecified"] if spec.domain == "pt" and not site_id else []
    for attempt in range(2):
        try:
            candidates = await storage.bounded(storage.candidates, directory, snapshot, terms, allowed, spec, site_id)
            break
        except storage.SnapshotMissing:
            if attempt:
                raise HTTPException(503, "knowledge_index_unavailable") from None
            await db.refresh(state)
            snapshot, generation = state.active_snapshot, state.active_generation
    verified = await metadata(db, principal, spec, site_id, candidates)
    chosen, seen = [], set()
    for candidate in candidates:
        key = candidate["document_id"], candidate["revision_number"]
        row = verified.get(key)
        if row is None or row.document_id in seen or applicability(row, spec):
            continue
        chosen.append(candidate)
        seen.add(row.document_id)
        if len(chosen) == spec.limit:
            break
    if chosen:
        await collections(db, principal)
        clauses = [(and_(KnowledgeDocument.id == item["document_id"],
                         KnowledgeRevision.number == item["revision_number"]),
                    func.substr(KnowledgeRevision.content, item["start_offset"] + 1, EXCERPT_CHARACTERS))
                   for item in chosen]
        rows = (await db.execute(metadata_statement(principal, spec, site_id).where(
            KnowledgeDocument.id.in_([item["document_id"] for item in chosen]),
        ).add_columns(case(*clauses, else_="").label("excerpt")))).all()
        verified = {(row.document_id, row.revision): row for row in rows}
        items, payloads, groups = [], [], {}
        budget = TOTAL_CHARACTERS
        for item in chosen:
            key = item["document_id"], item["revision_number"]
            row = verified.get(key)
            if row is None or applicability(row, spec):
                continue
            text = row.excerpt[:min(EXCERPT_CHARACTERS, budget)]
            if not text:
                continue
            budget -= len(text)
            items.append(evidence(row, item, text))
            payloads.append({"document": row.document_id, "revision": row.revision, "chunk": item["chunk_number"],
                             "start": item["start_offset"], "end": item["end_offset"],
                             "scope": {"product": spec.product, "product_version": spec.product_version,
                                       "domain": spec.domain, "site_id": site_id}})
            group = (row.domain, row.product, row.site_id, row.external_id, row.title,
                     row.version_scope, row.min_version, row.max_version)
            groups.setdefault(group, set()).add(row.content_hash)
            if row.version_scope == "unknown":
                notices.append("applicability_unknown")
            if not row.fetched_at:
                notices.append("fetched_time_unknown")
            if items[-1]["kind"] == "test":
                notices.append("test_reference_only")
            if budget <= 0:
                break
        if items:
            identifiers = await session.issue(principal, payloads)
            for item, identifier in zip(items, identifiers):
                item["evidence_id"] = identifier
            await db.refresh(state)
            if state.generation != generation:
                notices.append("index_updating")
            if site_id and all(item["site_id"] is None for item in items):
                notices.append("no_site_specific_match")
            variants = any(len(values) > 1 for values in groups.values())
            if variants:
                notices.append("source_variants_need_review")
            return response("conflicting" if variants else "matched", generation, items, notices)
    await collections(db, principal)
    await db.refresh(state)
    if state.generation != generation:
        raise HTTPException(503, "knowledge_index_updating", headers={"Retry-After": "5"})
    # 只在当前范围中查有界身份，区分不适用材料和完全没有匹配，不返回其正文。
    broad = await storage.bounded(storage.candidates, directory, snapshot, terms, allowed, spec, site_id, broad=True)
    known = await metadata(db, principal, spec, site_id, broad)
    reasons = {reason for row in known.values() if (reason := applicability(row, spec))}
    return response("not_applicable" if reasons else "no_match", generation, notices=[*notices, *reasons])


async def read(db, principal, identifier: str) -> dict:
    """
    只补读原引用附近上下文，实时读取 SQL 发布修订，不依赖历史快照或任意偏移。
    """
    payload = await session.resolve(principal, identifier)
    try:
        scope = payload["scope"]
        site_id = scope["site_id"]
        if site_id is not None and (not isinstance(site_id, str) or not re.fullmatch(r"[a-f0-9]{32}", site_id)):
            raise ValueError("invalid_reference_scope")
        spec = KnowledgeSearch(protocol_version=1, query="reference", product=scope["product"],
                               product_version=scope["product_version"], domain=scope["domain"])
        document, number, start, end, chunk = (payload[key] for key in ("document", "revision", "start", "end", "chunk"))
        if (not re.fullmatch(r"[a-f0-9]{32}", document) or type(number) is not int or number < 1
                or any(type(value) is not int for value in (start, end, chunk))
                or not 0 <= start < end <= 262144 or chunk < 0):
            raise ValueError("invalid_reference")
    except (KeyError, ValueError, TypeError):
        raise HTTPException(410, "knowledge_reference_expired") from None
    await collections(db, principal)
    statement = metadata_statement(principal, spec, site_id).where(
        KnowledgeDocument.id == document, KnowledgeRevision.number == number,
    )
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(410, "knowledge_reference_unavailable")
    reason = applicability(row, spec)
    if reason:
        return response("not_applicable", None, notices=[reason])
    beginning, length = max(0, start - 600), min(4000, end - start + 1200)
    excerpt = (await db.execute(statement.add_columns(
        func.substr(KnowledgeRevision.content, beginning + 1, length).label("excerpt"),
    ))).one_or_none()
    if excerpt is None:
        raise HTTPException(410, "knowledge_reference_unavailable")
    item = evidence(excerpt, {"chunk_number": chunk}, excerpt.excerpt)
    item["evidence_id"] = identifier
    notices = ["applicability_unknown"] if row.version_scope == "unknown" else []
    if not excerpt.fetched_at:
        notices.append("fetched_time_unknown")
    if item["kind"] == "test":
        notices.append("test_reference_only")
    return response("matched", None, [item], notices)
