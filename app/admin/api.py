"""后台 API 入口：所有业务读取和写入都需要有效账号及会话。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth, config, data, system, users
from app.admin.audit import record, user_dict
from app.admin.auth import AdminIdentity, administrator, current_identity, super_administrator
from app.admin.schemas import (
    CacheDeletePayload, ChangePasswordPayload, ConfirmPayload, CreateUserPayload, FeaturesPayload,
    LoginPayload, ResetPasswordPayload, UpdateUserPayload,
)
from app.db.deps import get_db
from app.db.redis import get_redis
from app.models.admin import AdminUser
from app.services.data_cleanup import data_cleanup_service

router = APIRouter(prefix="/admin/api", tags=["admin"])


@router.post("/auth/login")
async def login(payload: LoginPayload, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """通过账号密码登录，签发服务端会话。"""
    return await auth.login(payload, request, response, db)


@router.get("/auth/me")
async def me(identity: AdminIdentity = Depends(current_identity)):
    """页面刷新时恢复当前用户与 CSRF 标识。"""
    return {"user": user_dict(identity.user), "csrf_token": identity.session["csrf"]}


@router.post("/auth/logout")
async def logout(response: Response, identity: AdminIdentity = Depends(current_identity), db: AsyncSession = Depends(get_db)):
    """退出后在 Redis 撤销当前会话。"""
    await get_redis().delete(identity.key)
    response.delete_cookie(auth.COOKIE_NAME, path="/admin")
    record(db, identity.user, "auth.logout", identity.user.username)
    await db.commit()
    return {"ok": True}


@router.post("/auth/password")
async def password(payload: ChangePasswordPayload, response: Response,
                   identity: AdminIdentity = Depends(current_identity), db: AsyncSession = Depends(get_db)):
    """本人修改密码；所有设备需重新登录。"""
    await users.change_password(db, identity.user, payload.current_password, payload.password)
    await get_redis().delete(identity.key)
    response.delete_cookie(auth.COOKIE_NAME, path="/admin")
    return {"ok": True}


@router.get("/users")
async def list_users(q: str = Query("", max_length=100), page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                     _: AdminIdentity = Depends(super_administrator), db: AsyncSession = Depends(get_db)):
    """超级管理员分页查看账号，不返回密码哈希。"""
    query = select(AdminUser)
    if q:
        query = query.where(AdminUser.username.icontains(q, autoescape=True))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = (await db.scalars(query.order_by(AdminUser.id).offset((page - 1) * size).limit(size))).all()
    return {"items": [user_dict(user) for user in items], "total": total, "page": page, "size": size}


@router.post("/users")
async def create_user(payload: CreateUserPayload, identity: AdminIdentity = Depends(super_administrator), db: AsyncSession = Depends(get_db)):
    """创建需要首次修改密码的后台账号。"""
    try:
        return user_dict(await users.create_user(db, payload, identity.user))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "用户名已存在")


@router.patch("/users/{uid}")
async def update_user(uid: int, payload: UpdateUserPayload, identity: AdminIdentity = Depends(super_administrator), db: AsyncSession = Depends(get_db)):
    """编辑账号名称、角色及启用状态。"""
    return await users.update_user(db, uid, payload, identity.user)


@router.post("/users/{uid}/password")
async def reset_password(uid: int, payload: ResetPasswordPayload, identity: AdminIdentity = Depends(super_administrator), db: AsyncSession = Depends(get_db)):
    """重置临时密码并强制重新登录。"""
    await users.reset_password(db, uid, payload.password, identity.user)
    return {"ok": True}


@router.get("/overview")
async def overview(_: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """展示真实使用统计和最近共享。"""
    return await data.overview(db)


@router.get("/features")
async def features(_: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """读取有效功能配置及公开 GitHub 权限名单。"""
    return await config.read_features(db)


@router.put("/features")
async def save_features(payload: FeaturesPayload, identity: AdminIdentity = Depends(super_administrator), db: AsyncSession = Depends(get_db)):
    """对公共权限生效的配置修改只允许超级管理员执行。"""
    return await config.save_features(db, payload, identity.user)


@router.get("/recognition")
async def recognition(cursor: int = Query(0, ge=0), q: str = Query("", max_length=200), _: AdminIdentity = Depends(administrator)):
    """游标浏览共享识别缓存，不扫描其他 Redis 命名空间。"""
    return await data.list_recognition(cursor, q)


@router.delete("/recognition")
async def delete_recognition(payload: CacheDeletePayload, identity: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """确认后精确删除错误的识别缓存。"""
    require_confirmation(payload, "删除")
    await data.delete_recognition(db, payload.key, identity.user)
    return {"ok": True, "message": "缓存已删除；公共代理缓存最多仍可保留 10 分钟"}


@router.get("/system")
async def system_status(_: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """读取依赖状态、授权配置完整性和清理记录。"""
    return await system.status(db)


@router.get("/maintenance/preview")
async def cleanup_preview(_: AdminIdentity = Depends(super_administrator), db: AsyncSession = Depends(get_db)):
    """按现有清理规则预览影响范围。"""
    return await data_cleanup_service.preview(db)


@router.post("/maintenance/cleanup")
async def cleanup(payload: ConfirmPayload, identity: AdminIdentity = Depends(super_administrator)):
    """确认后执行带分布式锁和审计的清理。"""
    require_confirmation(payload, "清理")
    result = await data_cleanup_service.cleanup_once(identity.user.username)
    if result["status"] != "success":
        raise HTTPException(409 if result["status"] == "skipped" else 503, result["message"])
    return result


@router.get("/data/{resource}")
async def list_data(resource: str, q: str = Query("", max_length=200), page: int = Query(1, ge=1),
                    size: int = Query(20, ge=1, le=100), sort: str = "", descending: bool = True,
                    media_type: str = Query("", max_length=50), source: str = Query("", max_length=80),
                    _: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """分页读取白名单业务表；排序和筛选由服务端校验。"""
    return await data.list_data(db, resource, q, page, size, sort, descending, media_type, source)


@router.get("/data/{resource}/{item_id}")
async def detail(resource: str, item_id: int, _: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """读取业务详情，不修改分享复用次数。"""
    return await data.detail(db, resource, item_id)


@router.delete("/data/{resource}/{item_id}")
async def delete_data(resource: str, item_id: int, payload: ConfirmPayload,
                      identity: AdminIdentity = Depends(administrator), db: AsyncSession = Depends(get_db)):
    """按确认结果删除单条业务记录并审计。"""
    require_confirmation(payload, "删除")
    await data.delete_data(db, resource, item_id, identity.user)
    return {"ok": True}


def require_confirmation(payload: ConfirmPayload, expected: str) -> None:
    """服务端也校验危险操作确认，避免只在前端弹窗保护。"""
    if payload.confirm != expected:
        raise HTTPException(422, f"请输入“{expected}”确认操作")
