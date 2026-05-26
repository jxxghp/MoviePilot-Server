"""
FastAPI应用主文件
"""
import logging
from contextlib import asynccontextmanager

from starlette.background import BackgroundTask
from fastapi import FastAPI

from app.api.api import api_router
from app.core.config import settings
from app.db.database import engine
from app.db.redis import close_redis, init_redis
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

    yield
    # 关闭时清理资源
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

# 包含API路由（去掉全局前缀，直接挂载到根路径）
App.include_router(api_router)


@App.middleware("http")
async def record_request_user_middleware(request, call_next):
    """
    记录经过服务端接口的请求用户数量。
    """
    response = await call_next(request)
    if response.status_code >= 400 or RequestUserStatisticService.should_skip_request(request):
        return response

    if response.background:
        original_background = response.background

        async def record_with_original_background():
            """
            先执行原响应后台任务，再登记请求用户统计。
            """
            await original_background()
            await RequestUserStatisticService.safe_record_request_user(request)

        response.background = BackgroundTask(record_with_original_background)
    else:
        response.background = BackgroundTask(
            RequestUserStatisticService.safe_record_request_user,
            request,
        )

    return response


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
        timeout_keep_alive=settings.SERVER_TIMEOUT_KEEP_ALIVE
    )
