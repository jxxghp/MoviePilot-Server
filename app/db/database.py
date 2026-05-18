"""
数据库连接和会话管理
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine

from app.core.config import settings


def create_engine() -> AsyncEngine:
    """创建数据库引擎"""
    if settings.is_postgresql:
        # PostgreSQL引擎配置
        return create_async_engine(
            settings.database_url,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=settings.DB_POOL_RECYCLE,
            pool_timeout=settings.DB_POOL_TIMEOUT,
        )
    else:
        # SQLite引擎配置
        return create_async_engine(
            settings.database_url,
            echo=settings.DEBUG,
            pool_pre_ping=True,
        )


# 创建数据库引擎
engine = create_engine()

# 创建会话工厂
AsyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine
)
