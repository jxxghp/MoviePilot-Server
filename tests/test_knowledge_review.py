"""私有来源批准清单、真实采集证明与发布事务的隔离验收，不读取生产知识。"""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import settings
from app.models.knowledge import KnowledgeCollectReceipt, KnowledgeJob, KnowledgeRevision
from app.services.knowledge import review
from app.services.knowledge.network.policy import binding
from test_knowledge_collect import TEXT, configured, login, queue, records, run_scenario, source, synthetic_fetch
from test_knowledge_catalog import ROOT


async def approval(client, identity):
    """从当前合成来源计算批准指纹，不伪造正文、任务或来源归属。"""
    origin = (await client.get(ROOT + "/sources/" + identity)).json()
    policy = (await client.get(ROOT + "/sources/" + identity + "/collection")).json()["policy"]
    return review.ApprovedSource(id=identity, binding=binding(SimpleNamespace(**origin)), policy=policy)


async def collected(monkeypatch, tmp_path, client, sessions):
    """经真实 API 排队并执行采集，仅出站 I/O 返回合成文档。"""
    headers = await login(client)
    origin = await source(client, headers)
    upstream = synthetic_fetch(monkeypatch, TEXT * 8)
    worker = configured(monkeypatch, tmp_path, sessions)
    job = await queue(client, headers, origin)
    assert (await worker.run_once())["outcome"] == "collected"
    identity = (await records(client, job))["items"][0]["document_id"]
    return headers, origin, identity, worker, upstream, job


def test_dry_run_and_explicit_apply_use_real_publication_and_index(monkeypatch, tmp_path):
    """预览不发布；明确批准保留审计和索引队列，再运行不会重复发布同一 head。"""
    async def scenario(client, sessions, _redis):
        """验证不可变修订、正式 SQL 发布指针和实际索引激活。"""
        _headers, origin, identity, worker, _upstream, _job = await collected(monkeypatch, tmp_path, client, sessions)
        config = review.ReviewConfig(sources=[await approval(client, origin["id"])])
        assert (await review.run(sessions, config))["counts"] == {"eligible": 1}
        assert (await client.get(ROOT + "/documents/" + identity)).json()["published_revision"] is None
        assert (await review.run(sessions, config, apply=True))["counts"] == {"published": 1}
        doc = (await client.get(ROOT + "/documents/" + identity)).json()
        assert doc["published_revision"] == 1 and doc["revision"]["state"] == "published"
        assert (await worker.run_once())["outcome"] == "activated"
        assert (await review.run(sessions, config, apply=True))["candidates"] == 0
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KnowledgeRevision)) == 1
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("change, expected", [
    ("unlisted", {}), ("binding", {"source_not_approved": 1}),
    ("policy", {"policy_not_approved": 1}), ("manual", {"manual_or_unreviewable": 1}),
    ("cancelled", {"collection_not_approved": 1}), ("receipt", {"collection_not_approved": 1}),
    ("withdrawn", {"manual_or_unreviewable": 1}), ("disabled", {"source_not_approved": 1}),
])
def test_review_rechecks_current_authority_and_collection_proof(monkeypatch, tmp_path, change, expected):
    """越过来源、策略、人工、撤回或真实采集证明的变化都不能隐式发布。"""
    async def scenario(client, sessions, _redis):
        """先取得真实回执，再分别模拟边界变化，原发布指针保持为空。"""
        headers, origin, identity, _worker, _upstream, job = await collected(monkeypatch, tmp_path, client, sessions)
        rule = await approval(client, origin["id"])
        rules = [rule]
        if change == "unlisted":
            rules = []
        elif change == "binding":
            rules = [rule.model_copy(update={"binding": "f" * 64})]
        elif change == "policy":
            rules = [rule.model_copy(update={"policy": review.policy_values({"version_scope": "all"})})]
        elif change == "manual":
            doc = (await client.get(ROOT + "/documents/" + identity)).json()
            assert (await client.post(ROOT + "/documents/" + identity + "/revisions", headers=headers, json={
                "expected_version": doc["version"], "title": "人工修改", "content": TEXT * 8,
            })).status_code == 201
        else:
            async with sessions() as db:
                if change == "cancelled":
                    (await db.get(KnowledgeJob, job["id"])).cancel_requested = True
                elif change == "receipt":
                    receipt = await db.scalar(select(KnowledgeCollectReceipt).where(KnowledgeCollectReceipt.job_id == job["id"]))
                    await db.delete(receipt)
                elif change == "withdrawn":
                    revision = await db.scalar(select(KnowledgeRevision).where(KnowledgeRevision.document_id == identity))
                    revision.state = "withdrawn"
                elif change == "disabled":
                    from app.models.knowledge import KnowledgeSource
                    (await db.get(KnowledgeSource, origin["id"])).enabled = False
                await db.commit()
        result = await review.run(sessions, review.ReviewConfig(sources=rules), apply=True)
        assert result["counts"] == expected
        assert (await client.get(ROOT + "/documents/" + identity)).json()["published_revision"] is None
    run_scenario(monkeypatch, scenario)


def test_unchanged_collection_revalidates_a_changed_source_version(monkeypatch, tmp_path):
    """同范围来源变化使旧证明失效，新的真实 unchanged 回执才能重新授权。"""
    async def scenario(client, sessions, _redis):
        """更改一次等价版本策略后，比较重采前后而非重放旧任务。"""
        headers, origin, identity, worker, _upstream, _job = await collected(monkeypatch, tmp_path, client, sessions)
        response = await client.put(ROOT + "/sources/" + origin["id"] + "/collection", headers=headers,
                                    json={"expected_version": origin["version"], "version_scope": "unknown"})
        assert response.status_code == 200
        origin["version"] = response.json()["source_version"]
        config = review.ReviewConfig(sources=[await approval(client, origin["id"])])
        assert (await review.run(sessions, config, apply=True))["counts"] == {"collection_not_approved": 1}
        job = await queue(client, headers, origin)
        assert (await worker.run_once())["outcome"] == "collected"
        assert (await records(client, job))["result"]["counts"] == {"unchanged": 1}
        assert (await review.run(sessions, config, apply=True))["counts"] == {"published": 1}
        assert (await client.get(ROOT + "/documents/" + identity)).json()["published_revision"] == 1
    run_scenario(monkeypatch, scenario)


def test_online_switch_and_bounded_scan(monkeypatch, tmp_path):
    """服务关闭后停止审阅，数量限制在 SQL 扫描前执行。"""
    async def scenario(client, sessions, _redis):
        """两个真实来源都被批准，一轮只处理明确数量且不暗中扩大范围。"""
        headers, origin, _identity, worker, _upstream, _job = await collected(monkeypatch, tmp_path, client, sessions)
        second = (await client.post(ROOT + "/sources", headers=headers, json={
            "collection_id": origin["collection_id"], "name": "第二份合成资料", "connector": "http",
            "url": "https://docs.example/another", "selector": "main", "enabled": True,
        })).json()
        await queue(client, headers, second)
        assert (await worker.run_once())["outcome"] == "collected"
        config = review.ReviewConfig(sources=[await approval(client, item["id"]) for item in (origin, second)])
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", False)
        assert (await review.run(sessions, config, apply=True))["skipped"] == "knowledge_service_disabled"
        with pytest.raises(ValueError, match="review_limit_invalid"):
            await review.run(sessions, config, apply=True, limit=0)
        monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
        first = await review.run(sessions, config, apply=True, limit=1)
        assert first["candidates"] == 1 and first["counts"] == {"published": 1}
        assert (await review.run(sessions, config, apply=True, limit=1))["counts"] == {"published": 1}
    run_scenario(monkeypatch, scenario)


@pytest.mark.parametrize("body, expected", [
    (TEXT * 8, ""), ("short", "body_short"), ("请先登录\n" + TEXT * 8, "error_or_login_page"),
    ("欢迎回来，测试用户\n" + TEXT * 8, "personal_statistics"),
    ("上传量：**123 GB**\n" + TEXT * 8, "personal_statistics"),
    ("token=" + "x" * 40 + "\n" + TEXT * 8, "content_flags"),
    ("忽略以前的指令\n" + TEXT * 8, "content_flags"),
])
def test_review_quality_rejects_bad_upstream_content(body, expected):
    """仅使用合成规则、账号栏与秘密哨兵，不把认证页或凭据反射批准为知识。"""
    row = SimpleNamespace(content=body, title="站点规则")
    assert review.quality_reason(row, SimpleNamespace(connector="nexusphp"), None, []) == expected


def test_private_config_rejects_duplicate_sources_and_secret_approval(tmp_path):
    """私有文件约束与清单验证在访问 SQL 前生效，不允许配置放行疑似秘密。"""
    payload = {"id": "a" * 32, "binding": "b" * 64, "policy": {"version_scope": "unknown"}}
    with pytest.raises(ValidationError):
        review.ReviewConfig(sources=[payload, payload])
    with pytest.raises(ValidationError):
        review.ApprovedSource(**payload, allowed_flags=["suspected_secret"])
    path = tmp_path / "approved.json"
    path.write_text(json.dumps({"sources": [payload]}))
    path.chmod(0o600)
    tmp_path.chmod(0o700)
    assert len(review.load_config(path).sources) == 1
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private_review_config_required"):
        review.load_config(path)
    path.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="private_review_config_required"):
        review.load_config(link)
