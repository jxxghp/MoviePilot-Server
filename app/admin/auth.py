"""后台密码验证、限流和可随时撤销的 Redis 会话。"""

import hashlib
import json
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.admin.audit import now, record, user_dict
from app.admin.schemas import LoginPayload
from app.core.config import settings
from app.db.deps import get_db
from app.db.redis import get_redis
from app.models.admin import AdminUser

password_hasher = PasswordHash.recommended()
COOKIE_NAME = "mp_admin_session"
# 未知账号也校验 Argon2，避免通过明显的耗时差异枚举用户。
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


@dataclass
class AdminIdentity:
    """绑定当前数据库账号与经过验证的会话。"""

    user: AdminUser
    session: dict
    key: str


def session_key(token: str) -> str:
    """Redis 仅使用随机会话标识的摘要作为键。"""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"{settings.REDIS_KEY_PREFIX}:admin:session:{digest}"


async def hash_password(password: str) -> str:
    """在线程池执行内存密集的密码哈希，避免阻塞事件循环。"""
    return await run_in_threadpool(password_hasher.hash, password)


async def verify_password(password: str, encoded: str) -> bool:
    """在线程池验证密码；损坏的哈希按凭证错误处理。"""
    try:
        return await run_in_threadpool(password_hasher.verify, password, encoded)
    except Exception:
        # pwdlib 的损坏哈希异常类型来自可插拔后端，不将其内容返回客户端。
        return False


async def check_login_limit(request: Request, username: str) -> None:
    """同时限制来源地址和账号的登录尝试，使用共享 Redis 兼容多 worker。"""
    redis = get_redis()
    address = request.client.host if request.client else "unknown"
    for kind, value in (("ip", address), ("user", username)):
        digest = hashlib.sha256(value.encode()).hexdigest()
        key = f"{settings.REDIS_KEY_PREFIX}:admin:login:{kind}:{digest}"
        # 事务避免 incr 后进程中断留下永久限流键；NX 不延长现有窗口。
        async with redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, max(settings.ADMIN_LOGIN_WINDOW_SECONDS, 1), nx=True)
            count, _ = await pipeline.execute()
        if count > max(settings.ADMIN_LOGIN_ATTEMPTS, 1):
            raise HTTPException(429, "登录尝试过多，请稍后重试", headers={"Retry-After": str(max(await redis.ttl(key), 1))})


async def login(payload: LoginPayload, request: Request, response: Response, db: AsyncSession) -> dict:
    """验证账号密码后签发固定有效期会话，避免前端保存访问令牌。"""
    username = payload.username.strip().lower()
    await check_login_limit(request, username)
    user = await db.scalar(select(AdminUser).where(AdminUser.username == username))
    valid = await verify_password(payload.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid or not user.enabled:
        record(db, "anonymous", "auth.login_failed", username)
        await db.commit()
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    data = {"user_id": user.id, "auth_version": user.auth_version, "csrf": csrf}
    ttl = min(max(settings.ADMIN_SESSION_HOURS, 1), 168) * 3600
    user.last_login_at = now()
    record(db, user, "auth.login", user.username)
    await db.commit()
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        await get_redis().delete(session_key(old_token))
    await get_redis().set(session_key(token), json.dumps(data), ex=ttl)
    response.set_cookie(COOKIE_NAME, token, max_age=ttl, path="/admin", httponly=True,
                        secure=settings.ADMIN_COOKIE_SECURE, samesite="strict")
    return {"user": user_dict(user), "csrf_token": csrf}


async def current_identity(request: Request, db: AsyncSession = Depends(get_db)) -> AdminIdentity:
    """每次请求重新校验账号状态、会话版本及写操作 CSRF 标识。"""
    token = request.cookies.get(COOKIE_NAME, "")
    if not 32 <= len(token) <= 128:
        raise HTTPException(401, "请先登录管理后台")
    key = session_key(token)
    raw = await get_redis().get(key)
    try:
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict) or not isinstance(data.get("csrf"), str):
            raise ValueError("Invalid session")
        user = await db.get(AdminUser, data.get("user_id")) if data.get("user_id") else None
    except (ValueError, TypeError):
        user, data = None, {}
    if not user or not user.enabled or user.auth_version != data.get("auth_version"):
        raise HTTPException(401, "登录已失效，请重新登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not secrets.compare_digest(supplied, data.get("csrf", "")):
            raise HTTPException(403, "请求校验失败，请刷新页面后重试")
    return AdminIdentity(user=user, session=data, key=key)


async def administrator(identity: AdminIdentity = Depends(current_identity)) -> AdminIdentity:
    """临时密码会话只能修改本人密码，不能读取或操作业务数据。"""
    if identity.user.must_change_password:
        raise HTTPException(403, "请先修改初始密码")
    if identity.user.role not in {"admin", "super_admin"}:
        raise HTTPException(403, "没有管理权限")
    return identity


async def super_administrator(identity: AdminIdentity = Depends(administrator)) -> AdminIdentity:
    """账号与登录权限管理仅向超级管理员开放。"""
    if identity.user.role != "super_admin":
        raise HTTPException(403, "此操作需要超级管理员权限")
    return identity
