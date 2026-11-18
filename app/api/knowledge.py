"""
中心知识只读协议，独立凭据、短期证据与明确的错误语义。
"""

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.knowledge import KnowledgeRead, KnowledgeSearch
from app.services.knowledge.access import KnowledgePrincipal, knowledge_principal
from app.services.knowledge.retrieval import queries, session

REQUEST_SECONDS = 5
BODY_BYTES = 8192


class KnowledgeRoute(APIRoute):
    """
    所有协议响应禁用缓存；验证和故障不回显原查询、凭据、SQL 或本地路径。
    """

    def get_route_handler(self):
        """
        只约束知识协议，不改变其他 API 的异常和兼容行为。
        """
        handler = super().get_route_handler()

        async def guarded(request):
            """
            将可预期故障映射为稳定协议状态，不把不可用伪装成没有知识。
            """
            try:
                async with asyncio.timeout(REQUEST_SECONDS):
                    length = request.headers.get("content-length")
                    if length is not None and (not length.isdecimal() or int(length) > BODY_BYTES):
                        raise HTTPException(413, "knowledge_request_too_large")
                    if request.headers.get("content-encoding", "identity").lower() != "identity":
                        raise HTTPException(415, "knowledge_encoding_unsupported")
                    body = bytearray()
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > BODY_BYTES:
                            raise HTTPException(413, "knowledge_request_too_large")
                        body.extend(chunk)
                    request._body = bytes(body)
                    result = await handler(request)
            except RequestValidationError:
                result = JSONResponse({"protocol_version": 1, "status": "invalid_request",
                                       "detail": "knowledge_request_invalid"}, status_code=422)
            except HTTPException as error:
                status = {401: "unauthorized", 403: "forbidden", 410: "reference_expired",
                          413: "invalid_request", 415: "invalid_request", 422: "invalid_request",
                          429: "rate_limited", 503: "unavailable"}.get(error.status_code, "unavailable")
                result = JSONResponse({"protocol_version": 1, "status": status, "detail": error.detail},
                                      status_code=error.status_code, headers=error.headers)
            except (RedisError, SQLAlchemyError, OSError, sqlite3.Error, ValueError, TimeoutError):
                result = JSONResponse({"protocol_version": 1, "status": "unavailable",
                                       "detail": "knowledge_service_unavailable"}, status_code=503,
                                      headers={"Retry-After": "5"})
            result.headers["Cache-Control"] = "no-store"
            result.headers["Vary"] = "Authorization"
            return result

        return guarded


async def address_quota(request: Request) -> None:
    """
    使用 ASGI 连接身份限制匿名猜测，不信任客户端自报的转发头。
    """
    address = request.client.host if request.client else "unknown"
    await session.quota("address:" + address, session.ADDRESS_PER_MINUTE)


async def principal(identity: KnowledgePrincipal = Depends(knowledge_principal)) -> KnowledgePrincipal:
    """
    独立身份通过后使用共享客户端配额；普通后台 Cookie 不参与授权。
    """
    await session.quota("client:" + identity.client_id, session.CLIENT_PER_MINUTE)
    return identity


router = APIRouter(prefix="/knowledge/v1", tags=["knowledge"], route_class=KnowledgeRoute,
                   dependencies=[Depends(address_quota)])


@router.post("/search")
async def search(payload: KnowledgeSearch, identity: KnowledgePrincipal = Depends(principal),
                  db: AsyncSession = Depends(get_db)):
    """
    返回当前范围内少量已授权证据，不接受整段对话、全集导出或任意 URL 抓取。
    """
    return await queries.search(db, identity, payload)


@router.post("/read")
async def read(payload: KnowledgeRead, identity: KnowledgePrincipal = Depends(principal),
                db: AsyncSession = Depends(get_db)):
    """
    按本客户端短期引用补读相邻上下文，再次检查当前 SQL 权限与发布状态。
    """
    return await queries.read(db, identity, payload.evidence_id)
