"""
数据库会话管理工具
"""
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from .connection import db_connection


class DatabaseSessionManager:
    """数据库会话管理器"""
    
    def __init__(self):
        self._session: Optional[AsyncSession] = None
    
    async def get_session(self) -> AsyncSession:
        """获取数据库会话"""
        if self._session is None:
            self._session = db_connection.session_factory()
        return self._session
    
    async def close_session(self):
        """关闭数据库会话"""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def __aenter__(self) -> AsyncSession:
        """异步上下文管理器入口"""
        return await self.get_session()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close_session()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话的依赖注入函数
    :return: AsyncSession
    """
    async with DatabaseSessionManager() as session:
        try:
            yield session
        finally:
            await session.close()


# 向后兼容的函数名
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话 (向后兼容)
    :return: AsyncSession
    """
    async for session in get_db_session():
        yield session