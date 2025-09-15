"""
数据库升级脚本 - 为 subscribe_statistics 表添加 genre_ids 字段
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
import logging

logger = logging.getLogger(__name__)


async def upgrade_001_add_genre_ids_to_subscribe_statistics(engine: AsyncEngine):
    """
    升级脚本 001: 为 subscribe_statistics 表添加 genre_ids 字段
    
    Args:
        engine: 数据库引擎
    """
    try:
        async with engine.begin() as conn:
            # 检查字段是否已存在
            if engine.url.drivername == "postgresql+asyncpg":
                # PostgreSQL
                check_column_sql = text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'subscribe_statistics' 
                    AND column_name = 'genre_ids'
                """)
            else:
                # SQLite
                check_column_sql = text("""
                    SELECT name 
                    FROM pragma_table_info('subscribe_statistics') 
                    WHERE name = 'genre_ids'
                """)
            
            result = await conn.execute(check_column_sql)
            column_exists = result.fetchone() is not None
            
            if not column_exists:
                # 添加 genre_ids 字段
                add_column_sql = text("""
                    ALTER TABLE subscribe_statistics 
                    ADD COLUMN genre_ids VARCHAR
                """)
                await conn.execute(add_column_sql)
                logger.info("Successfully added genre_ids column to subscribe_statistics table")
            else:
                logger.info("genre_ids column already exists in subscribe_statistics table")
                
    except Exception as e:
        logger.error(f"Failed to add genre_ids column to subscribe_statistics table: {e}")
        raise


async def downgrade_001_remove_genre_ids_from_subscribe_statistics(engine: AsyncEngine):
    """
    降级脚本 001: 从 subscribe_statistics 表移除 genre_ids 字段
    
    Args:
        engine: 数据库引擎
    """
    try:
        async with engine.begin() as conn:
            # 检查字段是否存在
            if engine.url.drivername == "postgresql+asyncpg":
                # PostgreSQL
                check_column_sql = text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'subscribe_statistics' 
                    AND column_name = 'genre_ids'
                """)
            else:
                # SQLite
                check_column_sql = text("""
                    SELECT name 
                    FROM pragma_table_info('subscribe_statistics') 
                    WHERE name = 'genre_ids'
                """)
            
            result = await conn.execute(check_column_sql)
            column_exists = result.fetchone() is not None
            
            if column_exists:
                # 移除 genre_ids 字段
                drop_column_sql = text("""
                    ALTER TABLE subscribe_statistics 
                    DROP COLUMN genre_ids
                """)
                await conn.execute(drop_column_sql)
                logger.info("Successfully removed genre_ids column from subscribe_statistics table")
            else:
                logger.info("genre_ids column does not exist in subscribe_statistics table")
                
    except Exception as e:
        logger.error(f"Failed to remove genre_ids column from subscribe_statistics table: {e}")
        raise


# 升级脚本列表
UPGRADE_SCRIPTS = [
    ("001", "add_genre_ids_to_subscribe_statistics", upgrade_001_add_genre_ids_to_subscribe_statistics),
]

# 降级脚本列表
DOWNGRADE_SCRIPTS = [
    ("001", "remove_genre_ids_from_subscribe_statistics", downgrade_001_remove_genre_ids_from_subscribe_statistics),
]