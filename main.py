"""
FastAPI应用主文件
"""
import logging
from logging.config import dictConfig
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
import asyncio

from app.api.v1.api import api_router
from app.core.config import settings
from app.db.database import engine
from app.db.migrator import DatabaseMigrator
from app.models import Base


# 配置控制台日志（包含 uvicorn 访问日志与错误日志）
def _install_logging_config():
    log_level = "DEBUG" if settings.DEBUG else "INFO"

    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": "%(asctime)s - %(levelname)s - %(client_addr)s - \"%(request_line)s\" %(status_code)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "default",
                "level": log_level,
            },
            "access_console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "access",
                "level": log_level,
            },
        },
        "loggers": {
            # Uvicorn 自身日志（启动/错误）
            "uvicorn": {"handlers": ["console"], "level": log_level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": log_level, "propagate": False},
            # 访问日志（每个请求一条）
            "uvicorn.access": {"handlers": ["access_console"], "level": log_level, "propagate": False},
            # FastAPI / 应用日志
            "fastapi": {"handlers": ["console"], "level": log_level, "propagate": False},
        },
        # 根日志器，兜底
        "root": {"handlers": ["console"], "level": log_level},
    })


# 在导入模块后立即安装日志配置，确保尽早生效
_install_logging_config()

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

    # 执行数据库迁移
    try:
        # 在后台执行迁移，不阻塞启动
        async def run_migration_background():
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, lambda: DatabaseMigrator().upgrade())
            except Exception as mig_err:
                logger.error(f"Database migration failed: {mig_err}")

        asyncio.create_task(run_migration_background())
    except Exception as e:
        logger.error(f"Scheduling migration failed: {e}")

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
