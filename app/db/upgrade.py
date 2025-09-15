"""
数据库升级工具 - 使用 SQLAlchemy 的简单方案
"""
import logging
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def add_missing_columns(engine: AsyncEngine):
    """
    检查并添加缺失的列
    使用 SQLAlchemy 的 inspect 功能检查表结构，然后添加缺失的列
    """
    try:
        async with engine.begin() as conn:
            # 获取数据库检查器
            inspector = inspect(engine.sync_engine)
            
            # 检查 subscribe_statistics 表是否存在 genre_ids 列
            if 'subscribe_statistics' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('subscribe_statistics')]
                
                if 'genre_ids' not in columns:
                    logger.info("Adding genre_ids column to subscribe_statistics table...")
                    
                    # 添加 genre_ids 列
                    add_column_sql = text("""
                        ALTER TABLE subscribe_statistics 
                        ADD COLUMN genre_ids VARCHAR
                    """)
                    await conn.execute(add_column_sql)
                    logger.info("Successfully added genre_ids column to subscribe_statistics table")
                else:
                    logger.info("genre_ids column already exists in subscribe_statistics table")
            else:
                logger.info("subscribe_statistics table does not exist yet, will be created by create_all()")
                
    except Exception as e:
        logger.error(f"Failed to add missing columns: {e}")
        raise


async def upgrade_database(engine: AsyncEngine):
    """
    执行数据库升级
    1. 首先创建所有表（如果不存在）
    2. 然后添加缺失的列
    """
    logger.info("Starting database upgrade...")
    
    try:
        # 添加缺失的列
        await add_missing_columns(engine)
        
        logger.info("Database upgrade completed successfully")
        
    except Exception as e:
        logger.error(f"Database upgrade failed: {e}")
        raise