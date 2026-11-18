"""
显式配置本机专用临时 PostgreSQL 时，复用关键端到端合同并逐案例隔离 schema。
"""

import asyncio
import os
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import test_knowledge_access as access_tests
import test_knowledge_index as index_tests
import test_knowledge_jobs as job_tests

DATABASE_URL = os.environ.get("KNOWLEDGE_TEST_DATABASE_URL", "")


@pytest.mark.skipif(not DATABASE_URL, reason="requires dedicated local KNOWLEDGE_TEST_DATABASE_URL")
@pytest.mark.parametrize("case, options", [
    pytest.param(index_tests.test_publish_index_draft_history_and_withdraw, {}, id="publication-history"),
    pytest.param(index_tests.test_switches_queue_rebuild_and_reenable_restores_published_only,
                 {"kind": "sources"}, id="source-switch"),
    pytest.param(index_tests.test_generation_change_during_build_never_activates_stale_snapshot, {}, id="generation"),
    pytest.param(index_tests.test_running_cancel_prevents_activation_and_cleans_own_file, {}, id="running-cancel"),
    pytest.param(index_tests.test_activation_lost_commit_ack_keeps_possible_online_file, {}, id="lost-ack"),
    pytest.param(index_tests.test_concurrent_publish_and_withdraw_change_one_generation, {}, id="publication-race"),
    pytest.param(job_tests.test_two_real_connections_claim_one_lease_and_old_owner_is_fenced, {}, id="lease-race"),
    pytest.param(job_tests.test_new_request_while_cancel_pending_gets_own_slot, {}, id="cancel-slot"),
    pytest.param(access_tests.test_concurrent_rotation_has_one_winner_and_no_lost_grants, {}, id="credential-race"),
])
def test_postgresql_knowledge_contract(monkeypatch, tmp_path, case, options):
    """
    每次在本机 knowledge_test 数据库创建随机 schema；拒绝默认连接到业务数据库。
    """
    url = make_url(DATABASE_URL)
    assert url.drivername == "postgresql+asyncpg" and url.host in {"127.0.0.1", "localhost"}
    assert url.database == "knowledge_test", "必须使用显式专用测试数据库"
    schema = "knowledge_test_" + uuid4().hex
    module = sys.modules[case.__module__]
    original = module.run_scenario

    async def schema_action(create: bool):
        """
        使用独立短连接创建或清理本案例 schema，不更改其他 schema 的表。
        """
        engine = create_async_engine(DATABASE_URL, hide_parameters=True)
        try:
            async with engine.begin() as connection:
                command = f'CREATE SCHEMA "{schema}"' if create else f'DROP SCHEMA "{schema}" CASCADE'
                await connection.execute(text(command))
        finally:
            await engine.dispose()

    def isolated_scenario(mp, scenario, **kwargs):
        """
        保留原有真实 Cookie、CSRF 和 Bearer 探针，只替换为隔离 PostgreSQL 连接。
        """
        kwargs.update(database_url=DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
        return original(mp, scenario, **kwargs)

    asyncio.run(schema_action(True))
    try:
        monkeypatch.setattr(module, "run_scenario", isolated_scenario)
        case(monkeypatch, tmp_path, **options)
    finally:
        asyncio.run(schema_action(False))
