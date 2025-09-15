"""
数据库连接和会话管理
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine
from app.core.config import settings


class DatabaseConnection:
    """数据库连接管理类"""
    
    def __init__(self):
        self._engine = None
        self._session_factory = None
    
    def create_engine(self) -> AsyncEngine:
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
    
    @property
    def engine(self) -> AsyncEngine:
        """获取数据库引擎"""
        if self._engine is None:
            self._engine = self.create_engine()
        return self._engine
    
    @property
    def session_factory(self):
        """获取会话工厂"""
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
        return self._session_factory
    
    async def close(self):
        """关闭数据库连接"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None


# 全局数据库连接实例
db_connection = DatabaseConnection()

# 向后兼容的全局变量
engine = db_connection.engine
AsyncSessionLocal = db_connection.session_factory