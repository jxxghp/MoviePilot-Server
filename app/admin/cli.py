"""通过交互命令初始化或恢复超级管理员，密码不出现在命令行或日志中。"""

import argparse
import asyncio
import getpass

from fastapi import HTTPException
from sqlalchemy import select

from app.admin.audit import record
from app.admin.auth import hash_password
from app.admin.schemas import CreateUserPayload
from app.admin.users import create_user, lock_users
from app.db.database import AsyncSessionLocal, engine
from app.models import Base
from app.models.admin import AdminSetting, AdminUser
from app.services.database_schema import ensure_database_schema
from app.core.config import settings


async def bootstrap(username: str, password: str, recover: bool = False) -> None:
    """唯一配置键保护并发初始化；恢复仅允许操作既有超级管理员。"""
    await ensure_database_schema(engine, Base, settings.is_postgresql)
    async with AsyncSessionLocal() as db:
        if not recover:
            # 首次初始化的唯一键插入是原子门闩，不允许并发生成第二个初始用户。
            db.add(AdminSetting(key="__user_lock", value="1"))
            await db.flush()
            await create_user(db, CreateUserPayload(
                username=username, display_name=username, password=password, role="super_admin",
            ))
        else:
            await lock_users(db)
            user = await db.scalar(select(AdminUser).where(AdminUser.username == username.lower()))
            if not user or user.role != "super_admin":
                raise ValueError("只能恢复已存在的超级管理员")
            # 复用请求约束，恢复路径也遵循相同密码长度要求。
            CreateUserPayload(username=username, display_name=user.display_name, password=password)
            user.password_hash = await hash_password(password)
            user.enabled, user.must_change_password = True, True
            user.auth_version += 1
            record(db, "local-recovery", "user.recover", user.username)
            await db.commit()


def main() -> None:
    """从终端安全读取密码并执行一次初始化或恢复。"""
    parser = argparse.ArgumentParser(description="初始化 Server 后台超级管理员")
    parser.add_argument("username")
    parser.add_argument("--recover", action="store_true", help="恢复既有超级管理员并撤销旧会话")
    args = parser.parse_args()
    password = getpass.getpass("临时密码（至少 12 位）: ")
    if password != getpass.getpass("再次输入临时密码: "):
        parser.error("两次输入的密码不一致")
    try:
        asyncio.run(bootstrap(args.username, password, args.recover))
    except HTTPException as error:
        parser.exit(1, f"初始化失败：{error.detail}\n")
    except Exception:
        parser.exit(1, "初始化失败：请检查数据库、账号是否已存在以及密码是否符合要求。\n")
    print("账号已就绪，首次登录后必须修改临时密码。")


if __name__ == "__main__":
    main()
