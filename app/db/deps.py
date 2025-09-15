"""
数据库依赖注入
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from tools.database import get_db_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话
    :return: AsyncSession
    """
    async for session in get_db_session():
        yield session