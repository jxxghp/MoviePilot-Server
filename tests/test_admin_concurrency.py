"""通过独立连接并发修改超级管理员，验证数据库锁而非前端限制。"""

import asyncio

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.schemas import CreateUserPayload, UpdateUserPayload
from app.admin.users import create_user, update_user
from app.models import Base
from app.models.admin import AdminSetting, AdminUser


async def verify_last_superadmin(factory):
    """两个超级管理员同时停用自己，至多允许其中一个成功。"""
    async with factory() as db:
        db.add(AdminSetting(key="__user_lock", value="1"))
        await db.flush()
        first = await create_user(db, CreateUserPayload(username="first", display_name="First", password="test-password-first", role="super_admin"))
        first.must_change_password = False
        await db.commit()
        second = await create_user(db, CreateUserPayload(username="second", display_name="Second", password="test-password-second", role="super_admin"), first)
        second.must_change_password = False
        await db.commit()
        ids = [first.id, second.id]

    async def disable(uid):
        """每个请求持有独立事务与连接，不通过共享 Python 锁串行化。"""
        async with factory() as db:
            actor = await db.get(AdminUser, uid)
            try:
                await update_user(db, uid, UpdateUserPayload(display_name=actor.display_name, enabled=False, role="super_admin"), actor)
                return 200
            except HTTPException as error:
                await db.rollback()
                return error.status_code

    outcomes = await asyncio.gather(*(disable(uid) for uid in ids))
    assert sorted(outcomes) == [200, 409]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AdminUser).where(AdminUser.enabled.is_(True), AdminUser.role == "super_admin")) == 1


def test_last_superadmin_concurrent_sqlite(tmp_path):
    """文件数据库模拟不同 worker 的独立连接，而非共享内存连接。"""
    async def run():
        """创建和销毁隔离数据库，验证并发结果。"""
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/admin.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            await verify_last_superadmin(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    asyncio.run(run())
