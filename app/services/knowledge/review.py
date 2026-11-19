"""按中心私有白名单审阅真实采集修订，显式批准后复用正式发布与索引服务。"""

import argparse
import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select

from app.admin.config import knowledge_enabled
from app.models.knowledge import (
    KnowledgeCollectReceipt, KnowledgeCollectState, KnowledgeCollectTask, KnowledgeCollection, KnowledgeCredential,
    KnowledgeDocument, KnowledgeJob, KnowledgeRevision, KnowledgeSite, KnowledgeSource,
)
from app.schemas.knowledge import CollectPolicy
from app.services.knowledge import documents, jobs
from app.services.knowledge.checks import content_flags
from app.services.knowledge.network.policy import binding

ACTOR = "knowledge-source-review"


def policy_values(values: dict) -> dict:
    """复用采集版本范围校验，等价的省略空边界与显式 null 使用同一比较形式。"""
    return CollectPolicy(expected_version=1, **values).model_dump(exclude={"expected_version"})


class ApprovedSource(BaseModel):
    """部署维护者批准的来源、读取范围与策略；不能批准疑似秘密警告。"""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: dict
    allowed_flags: frozenset[Literal["content_instruction"]] = frozenset()

    @field_validator("policy")
    @classmethod
    def valid_policy(cls, values: dict) -> dict:
        """错误版本范围在扫描数据库前拒绝，不能靠宽松字典扩大批准范围。"""
        return policy_values(values)


class ReviewConfig(BaseModel):
    """仅存在于中心私有卷的批准清单，公开代码不携带任何真实来源身份。"""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    sources: list[ApprovedSource] = Field(max_length=5000)

    @model_validator(mode="after")
    def unique_sources(self):
        """拒绝同一身份重复配置，避免后者隐式覆盖前面的批准。"""
        if len({row.id for row in self.sources}) != len(self.sources):
            raise ValueError("duplicate_source")
        return self


def load_config(path: Path) -> ReviewConfig:
    """只读取受限大小、仅属主可访问的普通配置文件，不接受符号链接。"""
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or path.stat().st_mode & 0o077 or path.parent.stat().st_mode & 0o077
            or path.stat().st_size > 1024 * 1024):
        raise ValueError("private_review_config_required")
    return ReviewConfig.model_validate_json(path.read_bytes())


def quality_reason(revision, source, previous, allowed_flags) -> str:
    """拒绝短正文、登录或错误页、个人统计与异常替换，保留已有发布版。"""
    text = revision.content
    if len(text.strip()) < 120:
        return "body_short"
    if content_flags(revision.title + "\n" + text) - set(allowed_flags):
        return "content_flags"
    if re.search(r"(?im)^\s*(?:404(?:\s|$)|not found\s*$|access denied\s*$|just a moment|请先登录|会话已过期)", text[:500]):
        return "error_or_login_page"
    if re.search(r"欢迎回来|(?:上传量|下载量|魔力值|分享率)\s*[:：]\s*(?:\*{1,2})?\d", text):
        return "personal_statistics"
    if source.connector == "nexusphp" and not re.search(
            r"(?i)rules?|faq|规则|条款|守则|常见|问题|须知", revision.title + "\n" + text):
        return "unrecognized_site_document"
    if previous and len(previous.content) > 400 and not 0.4 <= len(text) / len(previous.content) <= 8:
        return "body_size_changed"
    return ""


async def collect_proof(db, document, job_id):
    """回读真实任务、策略与文档回执，不能只信修订的 created_by 字符串。"""
    job = await db.get(KnowledgeJob, job_id) if job_id else None
    task = await db.get(KnowledgeCollectTask, job_id) if job else None
    receipt = await db.scalar(select(KnowledgeCollectReceipt).where(
        KnowledgeCollectReceipt.job_id == job_id, KnowledgeCollectReceipt.document_id == document.id,
        KnowledgeCollectReceipt.revision_number == document.head_revision)) if task else None
    return job, task, receipt


async def inspect_document(db, identity: str, approvals: dict[str, ApprovedSource], *, apply: bool) -> str:
    """锁定当前事实和 head，保留人工、候选、撤回及失效采集，不直接改 SQL 发布指针。"""
    await jobs.lock_state(db)
    if not await knowledge_enabled(db):
        return "service_disabled"
    row = await db.get(KnowledgeDocument, identity, with_for_update=True)
    if row is None or row.head_revision == row.published_revision:
        return "already_current"
    source = await db.get(KnowledgeSource, row.source_id, with_for_update=True)
    rule = approvals.get(source.id)
    if not rule or not source.enabled or source.connector not in {"http", "nexusphp", "github"} or binding(source) != rule.binding:
        return "source_not_approved"
    policy = await db.get(KnowledgeCollectState, source.id)
    if not policy or policy_values(json.loads(policy.policy)) != rule.policy:
        return "policy_not_approved"
    parent = await db.get(KnowledgeCollection, source.collection_id)
    site = await db.get(KnowledgeSite, source.site_id) if source.site_id else None
    if not parent.enabled or (site and not site.enabled):
        return "parent_disabled"
    revision = await db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == row.id, KnowledgeRevision.number == row.head_revision))
    if revision is None or revision.state != "review" or not revision.created_by.startswith("knowledge-collect:"):
        return "manual_or_unreviewable"
    job, task, receipt = await collect_proof(db, row, revision.created_by.partition(":")[2])
    credential = await db.scalar(select(KnowledgeCredential.version).where(KnowledgeCredential.source_id == source.id)) or 0
    if task and (task.source_version != source.version or task.credential_version != credential):
        # 同一正文在当前配置下实采为 unchanged，才允许采用新的证明继续审阅。
        current = await collect_proof(db, row, policy.last_job_id)
        if current[2] and current[2].outcome == "unchanged":
            job, task, receipt = current
    if (not job or not task or not receipt or job.kind != "collect" or job.status != "succeeded"
            or job.cancel_requested or job.source_id != source.id or task.source_version != source.version
            or task.credential_version != credential or policy_values(json.loads(task.policy)) != rule.policy
            or receipt.outcome not in {"new", "updated", "unchanged"} or receipt.candidate_id
            or set(json.loads(receipt.flags)) - rule.allowed_flags):
        return "collection_not_approved"
    previous = await db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == row.id, KnowledgeRevision.number == row.published_revision)) if row.published_revision else None
    reason = quality_reason(revision, source, previous, rule.allowed_flags)
    if reason:
        return reason
    if apply:
        await documents.publish_document(db, row.id, row.version, row.head_revision, ACTOR)
    return "published" if apply else "eligible"


async def run(sessions, config: ReviewConfig, *, apply: bool = False, limit: int = 1000) -> dict:
    """有界扫描待审 head，逐篇独立事务；只输出计数，异常不包含正文、凭据或第三方消息。"""
    if not 1 <= limit <= 10000:
        raise ValueError("review_limit_invalid")
    approvals = {row.id: row for row in config.sources}
    counts = Counter()
    async with sessions() as db:
        if not await knowledge_enabled(db):
            return {"apply": False, "skipped": "knowledge_service_disabled", "counts": {}}
        identities = list(await db.scalars(select(KnowledgeDocument.id).where(
            KnowledgeDocument.source_id.in_(approvals), or_(KnowledgeDocument.published_revision.is_(None),
                KnowledgeDocument.head_revision != KnowledgeDocument.published_revision))
            .order_by(KnowledgeDocument.updated_at, KnowledgeDocument.id).limit(limit)))
    for identity in identities:
        async with sessions() as db:
            try:
                counts[await inspect_document(db, identity, approvals, apply=apply)] += 1
            except Exception:
                await db.rollback()
                counts["review_failed"] += 1
    return {"at": datetime.now(timezone.utc).isoformat(), "apply": apply, "sources": len(approvals),
            "candidates": len(identities), "counts": dict(counts)}


def main() -> None:
    """提供默认只预览的运维入口；真实批准必须显式传入 --apply。"""
    parser = argparse.ArgumentParser(description="审阅已明确批准来源的中心知识更新")
    parser.add_argument("--config", required=True, type=Path, help="中心私有批准清单的绝对路径")
    parser.add_argument("--apply", action="store_true", help="批准通过检查的明确采集修订")
    parser.add_argument("--limit", default=1000, type=int, help="本轮最多检查的文档数，范围 1-10000")
    arguments = parser.parse_args()

    async def execute():
        """按现有环境配置创建 SQL 会话，并在正常或异常退出时释放连接池。"""
        from app.db.database import AsyncSessionLocal, engine

        try:
            return await run(AsyncSessionLocal, load_config(arguments.config), apply=arguments.apply, limit=arguments.limit)
        finally:
            await engine.dispose()

    try:
        result = asyncio.run(execute())
    except Exception:
        print(json.dumps({"error": "knowledge_review_failed"}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result["counts"].get("review_failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
