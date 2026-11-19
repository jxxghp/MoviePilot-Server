"""
知识客户端签发、集合授权和逐请求认证；不提供知识正文或自动登记。
"""

import hashlib
import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import record
from app.core.config import settings
from app.db.deps import get_db
from app.models.knowledge import KnowledgeClient, KnowledgeCollection, KnowledgeGrant
from app.schemas.knowledge import ClientPayload, ClientUpdate
from app.services.knowledge.catalog import commit, get_row, replace_values

CLIENT_FIELDS = ("id", "version", "created_at", "updated_at", "name", "enabled", "expires_at")
bearer = HTTPBearer(auto_error=False, scheme_name="KnowledgeClient")


@dataclass(frozen=True)
class KnowledgePrincipal:
    """
    本次请求经中心验证的主体和有效集合范围，不接收调用方自报的 UID 或角色。

    :ivar client_id: 已验证的管理客户端或连接地址摘要
    :ivar version: 管理客户端授权版本；公共 Agent 主体固定为 1
    :ivar collection_ids: 本次可读的启用知识集合
    :ivar public: 是否只能读取已发布的公共知识
    """

    client_id: str
    version: int
    collection_ids: tuple[str, ...]
    public: bool = False

    def require_collection(self, collection_id: str) -> None:
        """
        对定位到的实际文档集合再检查范围，不能仅信任调用方传来的筛选条件。
        """
        if collection_id not in self.collection_ids:
            raise HTTPException(403, "没有此知识集合的读取权限")


def token_digest(token: str) -> str:
    """
    对中心生成的高熵随机令牌取摘要；此结果仅用于数据库匹配。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_address(request: Request) -> str:
    """
    仅在显式信任的反向代理地址读取其覆盖写入的客户端地址头。

    :param request: 当前 ASGI 请求
    :return: 可用于匿名引用隔离和配额的规范 IP 或连接地址
    """
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    trusted = settings.KNOWLEDGE_TRUSTED_PROXY_ADDRESSES.split(",")
    if not any(value.strip() and _matches_ip(peer_ip, value.strip()) for value in trusted):
        return peer
    forwarded = request.headers.get("x-real-ip", "")
    try:
        return ipaddress.ip_address(forwarded).compressed if forwarded else peer
    except ValueError:
        return peer


def _matches_ip(peer: ipaddress.IPv4Address | ipaddress.IPv6Address, value: str) -> bool:
    """比较已配置的代理 IP 或网段；错误配置不会扩大信任范围。"""
    try:
        network = ipaddress.ip_network(value, strict=False)
        return peer.version == network.version and peer in network
    except ValueError:
        return False


def client_metadata(row, collection_ids: list[str]) -> dict:
    """
    用固定字段构造管理投影，后续扩展模型也不会意外返回令牌摘要。
    """
    return {**{field: getattr(row, field) for field in CLIENT_FIELDS},
            "collection_ids": sorted(collection_ids)}


async def grant_ids(db: AsyncSession, client_id: str) -> list[str]:
    """
    管理界面保留停用集合的授权记录，便于解释范围与重新维护。
    """
    return list((await db.scalars(select(KnowledgeGrant.collection_id).where(
        KnowledgeGrant.client_id == client_id,
    ).order_by(KnowledgeGrant.collection_id))).all())


async def validate_grants(db: AsyncSession, collection_ids: list[str], client_id: str | None = None) -> None:
    """
    新增授权只能指向启用集合；已有停用集合可原样保留，但认证时始终剔除。
    """
    if not collection_ids:
        return
    rows = (await db.execute(select(KnowledgeCollection.id, KnowledgeCollection.enabled).where(
        KnowledgeCollection.id.in_(collection_ids),
    ).order_by(KnowledgeCollection.id).with_for_update())).all()
    retained = set(await grant_ids(db, client_id)) if client_id is not None else set()
    if len(rows) != len(collection_ids) or any(not row.enabled and row.id not in retained for row in rows):
        raise HTTPException(422, "只能新增对现有启用集合的授权")


async def list_clients(db: AsyncSession, q: str, page: int, size: int) -> dict:
    """
    按页批量展示标签与授权，查询和响应都不包含秘密摘要。
    """
    conditions = [KnowledgeClient.name.icontains(q, autoescape=True)] if q else []
    total = await db.scalar(select(func.count()).select_from(KnowledgeClient).where(*conditions))
    rows = (await db.execute(select(*(getattr(KnowledgeClient, field) for field in CLIENT_FIELDS))
                            .where(*conditions).order_by(KnowledgeClient.created_at.desc(), KnowledgeClient.id)
                            .offset((page - 1) * size).limit(size))).all()
    grouped = {row.id: [] for row in rows}
    if grouped:
        grants = (await db.execute(select(KnowledgeGrant.client_id, KnowledgeGrant.collection_id).where(
            KnowledgeGrant.client_id.in_(grouped),
        ))).all()
        for grant in grants:
            grouped[grant.client_id].append(grant.collection_id)
    return {"items": [client_metadata(row, grouped[row.id]) for row in rows],
            "total": total or 0, "page": page, "size": size}


async def read_client(db: AsyncSession, client_id: str) -> dict:
    """
    重新读取客户端版本和全部授权，不支持找回旧令牌。
    """
    row = await get_row(db, KnowledgeClient, client_id)
    return client_metadata(row, await grant_ids(db, client_id))


async def create_client(db: AsyncSession, payload: ClientPayload, actor: str) -> dict:
    """
    在同一事务保存令牌摘要、授权及审计，明文仅包含在本次成功结果中。
    """
    await validate_grants(db, payload.collection_ids)
    token = "mpk_" + secrets.token_urlsafe(32)
    row = KnowledgeClient(**payload.model_dump(mode="json", exclude={"collection_ids"}),
                          token_hash=token_digest(token))
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, "知识客户端签发冲突，请重试") from error
    db.add_all(KnowledgeGrant(client_id=row.id, collection_id=item) for item in payload.collection_ids)
    record(db, actor, "knowledge.client.create", row.id, {"grant_count": len(payload.collection_ids)})
    result = client_metadata(row, payload.collection_ids)
    await commit(db)
    return {**result, "token": token}


async def update_client(db: AsyncSession, client_id: str, payload: ClientUpdate, actor: str) -> dict:
    """
    乐观版本保护状态和完整范围的原子替换；不重置或返回已有令牌。
    """
    row = await get_row(db, KnowledgeClient, client_id, for_update=True)
    await validate_grants(db, payload.collection_ids, client_id)
    await replace_values(db, row, payload.expected_version,
                         payload.model_dump(mode="json", exclude={"collection_ids", "expected_version"}))
    await db.execute(delete(KnowledgeGrant).where(KnowledgeGrant.client_id == client_id))
    db.add_all(KnowledgeGrant(client_id=client_id, collection_id=item) for item in payload.collection_ids)
    await db.flush()
    await db.refresh(row)
    record(db, actor, "knowledge.client.update", client_id, {
        "grant_count": len(payload.collection_ids), "enabled": payload.enabled, "version": row.version,
    })
    result = client_metadata(row, payload.collection_ids)
    await commit(db)
    return result


async def rotate_client(db: AsyncSession, client_id: str, expected_version: int, actor: str) -> dict:
    """
    原子替换当前摘要，成功后旧令牌失效；不缓存明文供重试或找回。
    """
    row = await get_row(db, KnowledgeClient, client_id, for_update=True)
    token = "mpk_" + secrets.token_urlsafe(32)
    await replace_values(db, row, expected_version, {"token_hash": token_digest(token)})
    await db.refresh(row)
    record(db, actor, "knowledge.client.rotate", client_id, {"version": row.version})
    result = client_metadata(row, await grant_ids(db, client_id))
    await commit(db)
    return {**result, "token": token}


async def authenticate(db: AsyncSession, token: str) -> KnowledgePrincipal:
    """
    每次请求读取当前摘要、状态、时限和集合开关，不用共享缓存延迟撤销。
    """
    from app.admin.config import knowledge_enabled

    if not await knowledge_enabled(db):
        raise HTTPException(503, "中心知识服务尚未启用")
    unauthorized = HTTPException(401, "知识访问凭据无效或已失效", headers={"WWW-Authenticate": "Bearer"})
    if not re.fullmatch(r"mpk_[A-Za-z0-9_-]{43}", token):
        raise unauthorized
    row = (await db.execute(select(KnowledgeClient.id, KnowledgeClient.version, KnowledgeClient.expires_at).where(
        KnowledgeClient.token_hash == token_digest(token), KnowledgeClient.enabled.is_(True),
    ))).one_or_none()
    if row is None:
        raise unauthorized
    if row.expires_at is not None:
        try:
            deadline = datetime.fromisoformat(row.expires_at)
            if deadline.utcoffset() is None or deadline <= datetime.now(timezone.utc):
                raise unauthorized
        except (ValueError, TypeError) as error:
            raise unauthorized from error
    granted = tuple((await db.scalars(select(KnowledgeGrant.collection_id).join(
        KnowledgeCollection, KnowledgeCollection.id == KnowledgeGrant.collection_id,
    ).where(KnowledgeGrant.client_id == row.id, KnowledgeCollection.enabled.is_(True))
        .order_by(KnowledgeGrant.collection_id))).all())
    if not granted:
        raise HTTPException(403, "此客户端没有可用的知识集合授权")
    return KnowledgePrincipal(client_id=row.id, version=row.version, collection_ids=granted)


async def knowledge_principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                              db: AsyncSession = Depends(get_db)) -> KnowledgePrincipal:
    """
    为只读知识工具提供 Bearer 依赖，不接受后台 Cookie、推送令牌或自报身份替代。
    """
    token = credentials.credentials if credentials is not None and credentials.scheme.lower() == "bearer" else ""
    return await authenticate(db, token)


async def knowledge_agent_principal(request: Request, db: AsyncSession = Depends(get_db)) -> KnowledgePrincipal:
    """
    为所有 MP Agent 提供公开发布集合的只读主体；引用按连接地址摘要隔离。

    :param request: 当前 HTTP 连接，转发头不作为身份依据
    :param db: 读取当前启用的公开知识集合
    :return: 不具备后台权限的公共检索主体
    """
    from app.admin.config import knowledge_enabled

    if not await knowledge_enabled(db):
        raise HTTPException(503, "中心知识服务尚未启用")
    address = request_address(request)
    client_id = "agent_" + hashlib.sha256(address.encode("utf-8")).hexdigest()
    rows = (await db.execute(select(KnowledgeCollection.id, KnowledgeCollection.domain,
                                   KnowledgeCollection.product).where(
        KnowledgeCollection.enabled.is_(True),
    ).order_by(KnowledgeCollection.id))).all()
    return KnowledgePrincipal(client_id=client_id, version=1,
                              collection_ids=tuple(row.id for row in rows), public=True)
