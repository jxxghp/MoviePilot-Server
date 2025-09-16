"""
FastAPI应用主文件
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.api import api_router
from app.core.config import settings
from app.db.database import engine
from app.models import Base

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    应用生命周期管理
    """
    # 启动时初始化数据库（SQLite 才执行，PostgreSQL 跳过以避免等待数据库就绪）
    try:
        if not settings.is_postgresql:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            logger.info("Skipping database schema init at startup for PostgreSQL")
    except Exception as e:
        logger.warning(f"Database init skipped due to error: {e}")

    yield
    # 关闭时清理资源
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
        reload=settings.DEBUG
    )
