"""后台账号生命周期，串行保护最后一个可用超级管理员。"""

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import now, record, user_dict
from app.admin.auth import hash_password, verify_password
from app.admin.schemas import CreateUserPayload, UpdateUserPayload
from app.models.admin import AdminSetting, AdminUser


async def lock_users(db: AsyncSession, actor: AdminUser | None = None) -> None:
    """通过同一行写锁串行化用户变更，同时兼容 SQLite 与 PostgreSQL。"""
    version = actor.auth_version if actor else None
    result = await db.execute(update(AdminSetting).where(AdminSetting.key == "__user_lock").values(value="1"))
    if not result.rowcount:
        raise HTTPException(503, "请先使用初始化命令创建超级管理员")
    if actor:
        await db.refresh(actor)
        if actor.auth_version != version:
            raise HTTPException(401, "登录已失效，请重新登录")
        if not actor.enabled or actor.role != "super_admin" or actor.must_change_password:
            raise HTTPException(403, "此操作需要有效的超级管理员权限")


async def create_user(db: AsyncSession, payload: CreateUserPayload, actor: AdminUser | None = None) -> AdminUser:
    """创建后台账号；初始化路径只允许在账号表为空时执行。"""
    if actor:
        await lock_users(db, actor)
    elif await db.scalar(select(func.count()).select_from(AdminUser)):
        raise HTTPException(409, "已存在后台用户，请通过超级管理员管理账号")
    if await db.scalar(select(AdminUser.id).where(AdminUser.username == payload.username.lower())):
        raise HTTPException(409, "用户名已存在")
    user = AdminUser(
        username=payload.username.lower(), display_name=payload.display_name.strip(),
        password_hash=await hash_password(payload.password), role=payload.role,
        enabled=True, must_change_password=True, auth_version=1, created_at=now(),
    )
    if not user.display_name:
        raise HTTPException(422, "显示名称不能为空")
    db.add(user)
    await db.flush()
    record(db, actor or "bootstrap", "user.create", user.username, {"role": user.role})
    await db.commit()
    return user


async def update_user(db: AsyncSession, uid: int, payload: UpdateUserPayload, actor: AdminUser) -> dict:
    """变更角色或启用状态时撤销所有会话，并保留至少一个可用超级管理员。"""
    await lock_users(db, actor)
    user = await db.get(AdminUser, uid, populate_existing=True)
    if not user:
        raise HTTPException(404, "用户不存在")
    if user.enabled and user.role == "super_admin" and (not payload.enabled or payload.role != "super_admin"):
        count = await db.scalar(select(func.count()).select_from(AdminUser).where(
            AdminUser.enabled.is_(True), AdminUser.role == "super_admin",
        ))
        if count <= 1:
            raise HTTPException(409, "必须保留至少一个启用的超级管理员")
    name = payload.display_name.strip()
    if not name:
        raise HTTPException(422, "显示名称不能为空")
    before = {"role": user.role, "enabled": user.enabled, "display_name": user.display_name}
    if user.role != payload.role or user.enabled != payload.enabled:
        user.auth_version += 1
    user.role, user.enabled, user.display_name = payload.role, payload.enabled, name
    record(db, actor, "user.update", user.username, {"before": before, "after": payload.model_dump()})
    await db.commit()
    return user_dict(user)


async def reset_password(db: AsyncSession, uid: int, password: str, actor: AdminUser) -> None:
    """重置临时密码并撤销目标用户的全部会话。"""
    await lock_users(db, actor)
    user = await db.get(AdminUser, uid, populate_existing=True)
    if not user:
        raise HTTPException(404, "用户不存在")
    user.password_hash = await hash_password(password)
    user.auth_version += 1
    user.must_change_password = True
    record(db, actor, "user.reset_password", user.username)
    await db.commit()


async def change_password(db: AsyncSession, user: AdminUser, current: str, password: str) -> None:
    """校验当前密码后完成修改，首次修改同时解除临时密码限制。"""
    # 同一用户的密码重置与修改使用同一行锁，防止旧密码覆盖刚完成的重置。
    await db.execute(update(AdminSetting).where(AdminSetting.key == "__user_lock").values(value="1"))
    await db.refresh(user)
    if not user.enabled or not await verify_password(current, user.password_hash):
        raise HTTPException(400, "当前密码不正确")
    if current == password:
        raise HTTPException(422, "新密码不能与当前密码相同")
    user.password_hash = await hash_password(password)
    user.auth_version += 1
    user.must_change_password = False
    record(db, user, "user.change_password", user.username)
    await db.commit()
