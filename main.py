"""
FastAPI应用主文件
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.api import api_router
from app.core.config import settings
from app.db.database import engine
from app.db.redis import close_redis, init_redis, log_connection_error
from app.models import Base
from app.services.data_cleanup import data_cleanup_service
from app.services.database_schema import ensure_database_schema
from app.services.media_recognize_share import media_recognize_share_service
from app.services.request_user_statistic import RequestUserStatisticService
from app.services.tmdb import tmdb_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    应用生命周期管理
    """
    # 启动时初始化数据库结构，PostgreSQL 使用事务级锁避免多 worker 并发建表冲突。
    try:
        await ensure_database_schema(engine, Base, settings.is_postgresql)
    except Exception as e:
        logger.warning(f"Database init skipped due to error: {e}")

    # 初始化Redis并启动共享识别缓存服务。
    await init_redis()
    await media_recognize_share_service.start()
    await data_cleanup_service.start()
    await RequestUserStatisticService.start()

    yield
    # 关闭时清理资源
    await RequestUserStatisticService.stop()
    await data_cleanup_service.stop()
    await media_recognize_share_service.stop()
    await tmdb_service.close()
    await close_redis()
    await engine.dispose()


# 创建FastAPI应用实例
App = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan
)


@App.exception_handler(RedisConnectionError)
async def redis_connection_error_handler(
        _: Request,
        err: RedisConnectionError,
) -> JSONResponse:
    """将 Redis 连接故障转换为可退避响应，避免异常栈放大过载。"""
    log_connection_error(err)
    return JSONResponse(
        status_code=503,
        content={"detail": "Service temporarily unavailable"},
        headers={
            "Retry-After": "1",
            "Cache-Control": "no-store",
        },
    )


class RequestUserStatisticMiddleware:
    """在成功响应完成后将请求用户加入异步统计队列。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if RequestUserStatisticService.should_skip_request(request):
            await self.app(scope, receive, send)
            return

        status_code = 500

        async def capture_status(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, capture_status)
        if status_code < 400:
            RequestUserStatisticService.enqueue_request_user(request)


App.add_middleware(RequestUserStatisticMiddleware)

# 包含API路由（去掉全局前缀，直接挂载到根路径）
App.include_router(api_router)


@App.get("/")
async def root():
    """根路径"""
    return {
        "code": 0,
        "message": f"{settings.APP_NAME} is running ..."
    }


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        'main:App',
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=settings.server_workers,
        backlog=settings.SERVER_BACKLOG,
        limit_concurrency=settings.server_limit_concurrency,
        timeout_keep_alive=settings.SERVER_TIMEOUT_KEEP_ALIVE,
        access_log=settings.SERVER_ACCESS_LOG,
    )
