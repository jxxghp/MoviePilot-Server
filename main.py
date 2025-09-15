"""
FastAPI应用主文件
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.db.database import engine
from app.models import Base
from app.api.v1.api import api_router
from app.db.migrations.upgrader import DatabaseUpgrader
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    """
    # 启动时初始化数据库
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 执行数据库升级
    try:
        upgrader = DatabaseUpgrader(engine)
        await upgrader.upgrade()
    except Exception as e:
        logger.error(f"Database upgrade failed: {e}")
        raise
    
    yield
    # 关闭时清理资源
    await engine.dispose()


# 创建FastAPI应用实例
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan
)

# 包含API路由
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """根路径"""
    return {
        "code": 0,
        "message": f"{settings.APP_NAME} is running ..."
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'main:app', 
        host=settings.HOST, 
        port=settings.PORT, 
        reload=settings.DEBUG
    )