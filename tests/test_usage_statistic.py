"""安装版本统计的上报过滤和报表筛选测试。"""

import asyncio
from unittest.mock import AsyncMock, Mock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.cache import cache_manager
from app.models.base import Base
from app.models.usage_statistic import IOSUsageStatistics, UsageStatistics
from app.schemas.models import IOSUsageStatisticItem, UsageStatisticItem
from app.services.usage_statistic import UsageService


def test_usage_report_ignores_requests_without_user_or_version(monkeypatch) -> None:
    """缺少用户 ID 或版本号的上报不写入数据库。"""

    async def run_scenario() -> None:
        """使用模拟持久化边界验证上报条件。"""
        record_usage = AsyncMock()
        invalidate_cache = Mock()
        monkeypatch.setattr(UsageService, "_record_usage", record_usage)
        monkeypatch.setattr(UsageService, "_invalidate_statistics_cache", invalidate_cache)
        db = AsyncMock()

        cases = [
            UsageStatisticItem(backend_version="v2.0.0"),
            UsageStatisticItem(user_uid="user-1"),
            UsageStatisticItem(user_uid=" ", backend_version="v2.0.0"),
            UsageStatisticItem(user_uid="user-2", backend_version=" "),
        ]
        for usage in cases:
            result = await UsageService.report_usage(db, usage)
            assert result == {"code": 0, "message": "success"}

        record_usage.assert_not_awaited()
        db.commit.assert_not_awaited()
        invalidate_cache.assert_not_called()

    asyncio.run(run_scenario())


def test_usage_report_records_identified_version(monkeypatch) -> None:
    """同时包含用户 ID 和版本号的上报会累计安装统计。"""

    async def run_scenario() -> None:
        """使用模拟持久化边界验证上报条件。"""
        record_usage = AsyncMock()
        invalidate_cache = Mock()
        monkeypatch.setattr(UsageService, "_record_usage", record_usage)
        monkeypatch.setattr(UsageService, "_invalidate_statistics_cache", invalidate_cache)
        db = AsyncMock()
        usage = UsageStatisticItem(
            user_uid="user-1",
            backend_version="v2.0.0",
        )

        result = await UsageService.report_usage(db, usage)

        assert result == {"code": 0, "message": "success"}
        record_usage.assert_awaited_once()
        db.commit.assert_awaited_once()
        invalidate_cache.assert_called_once()

    asyncio.run(run_scenario())


def test_statistics_response_does_not_include_unknown_users(monkeypatch) -> None:
    """统计报表只返回已识别安装数，不再合并 Redis 未知用户。"""

    async def run_scenario() -> None:
        """隔离缓存并检查统计返回契约。"""
        cache_manager.usage_statistic_cache.clear()
        monkeypatch.setattr(
            UsageStatistics,
            "list_backend_version_counts",
            AsyncMock(return_value=[("v2.0.0", 2)]),
        )
        monkeypatch.setattr(
            UsageStatistics,
            "list_frontend_version_counts",
            AsyncMock(return_value=[("v2.0.0", 2)]),
        )
        monkeypatch.setattr(IOSUsageStatistics, "list_version_counts", AsyncMock(return_value=[("1.0.6", 3)]))
        monkeypatch.setattr(UsageStatistics, "count_all", AsyncMock(return_value=2))
        monkeypatch.setattr(
            UsageStatistics,
            "count_active_since",
            AsyncMock(side_effect=[1, 2, 2]),
        )

        result = await UsageService.get_statistics(AsyncMock())

        assert result["total_users"] == 2
        assert result["backend_versions"] == [{"version": "v2.0.0", "count": 2}]
        assert result["frontend_versions"] == [{"version": "v2.0.0", "count": 2}]
        assert result["ios_app_versions"] == [{"version": "1.0.6", "count": 3}]
        assert "other_users" not in result
        assert "reported_users" not in result
        cache_manager.usage_statistic_cache.clear()

    asyncio.run(run_scenario())


def test_statistics_exclude_records_without_user_or_version() -> None:
    """报表忽略历史上缺少用户 ID 或所有版本号的记录。"""

    async def run_scenario() -> None:
        """通过内存数据库验证有效实例过滤。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            async with session_factory() as db:
                db.add_all([
                    UsageStatistics(
                        user_uid="user-1",
                        backend_version="v2.0.0",
                        frontend_version="v2.0.0",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                    UsageStatistics(
                        user_uid="user-2",
                        frontend_version="v2.0.0",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                    UsageStatistics(
                        user_uid="user-3",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                    UsageStatistics(
                        user_uid=" ",
                        backend_version="v2.0.0",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                ])
                await db.commit()

                assert await UsageStatistics.count_all(db) == 2
                assert await UsageStatistics.count_active_since(
                    db,
                    "2026-08-05 00:00:00",
                ) == 2
                assert await UsageStatistics.list_backend_version_counts(db) == [
                    ("v2.0.0", 1),
                ]
                assert await UsageStatistics.list_frontend_version_counts(db) == [
                    ("v2.0.0", 2),
                ]
        finally:
            await engine.dispose()

    asyncio.run(run_scenario())


def test_ios_installation_upgrade_is_independent_of_backend_counts() -> None:
    """重复、多实例和升级上报均按一个 App 安装计数，不污染后端数量。"""
    async def run_scenario() -> None:
        """在独立内存库中验证新增表、去重和版本迁移。"""
        from uuid import UUID
        from sqlalchemy import select
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine)() as db:
                for version in ["1.0.5", "1.0.5", "1.0.6"]:
                    await UsageService.report_ios_usage(db, IOSUsageStatisticItem(
                        installation_id=UUID(int=1), version=version))
                await UsageService.report_ios_usage(db, IOSUsageStatisticItem(
                    installation_id=UUID(int=2), version="1.0.6"))
                assert await IOSUsageStatistics.list_version_counts(db) == [("1.0.6", 2)]
                assert await UsageStatistics.count_all(db) == 0
                records = (await db.execute(select(IOSUsageStatistics))).scalars().all()
                assert len(records) == 2
                assert all(row.first_seen_at and row.last_seen_at for row in records)
        finally:
            cache_manager.usage_statistic_cache.clear()
            await engine.dispose()
    asyncio.run(run_scenario())


def test_ios_report_rejects_invalid_identity_and_version() -> None:
    """iOS 公共上报边界拒绝非法标识、空版本及超长版本。"""
    from uuid import UUID
    import pytest
    from pydantic import ValidationError
    for version in ["", " ", "x" * 65, "unknown"]:
        with pytest.raises(ValidationError):
            IOSUsageStatisticItem(installation_id=UUID(int=1), version=version)
    with pytest.raises(ValidationError):
        IOSUsageStatisticItem(installation_id="invalid", version="1.0.6")
