"""
数据库依赖注入
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话
    :return: AsyncSession
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()