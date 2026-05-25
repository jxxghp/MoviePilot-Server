"""
数据库结构初始化服务
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

POSTGRESQL_SCHEMA_LOCK_ID = 2026052501


async def ensure_database_schema(engine: AsyncEngine, base: Any, is_postgresql: bool) -> None:
    """
    确保当前数据库中存在所有已注册模型表。
    """
    if is_postgresql:
        await ensure_postgresql_schema(engine, base)
        return

    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)


async def ensure_postgresql_schema(engine: AsyncEngine, base: Any) -> None:
    """
    在PostgreSQL事务级锁保护下创建所有已注册模型表。
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": POSTGRESQL_SCHEMA_LOCK_ID},
        )
        await conn.run_sync(base.metadata.create_all)
