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
            pool_size=20,
            max_overflow=10,
            pool_recycle=3600,
            pool_timeout=180,
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
