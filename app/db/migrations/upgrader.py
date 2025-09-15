"""
数据库升级管理器
"""
import logging
from typing import List, Tuple, Callable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from app.db.migrations import UPGRADE_SCRIPTS, DOWNGRADE_SCRIPTS

logger = logging.getLogger(__name__)


class DatabaseUpgrader:
    """数据库升级管理器"""
    
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
    
    async def create_migration_table(self):
        """创建迁移记录表"""
        async with self.engine.begin() as conn:
            # 检查表是否存在
            if self.engine.url.drivername == "postgresql+asyncpg":
                # PostgreSQL
                check_table_sql = text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'database_migrations'
                    )
                """)
            else:
                # SQLite
                check_table_sql = text("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='database_migrations'
                """)
            
            result = await conn.execute(check_table_sql)
            table_exists = result.fetchone()[0] if self.engine.url.drivername == "postgresql+asyncpg" else result.fetchone() is not None
            
            if not table_exists:
                create_table_sql = text("""
                    CREATE TABLE database_migrations (
                        id INTEGER PRIMARY KEY,
                        version VARCHAR(50) NOT NULL UNIQUE,
                        description VARCHAR(255),
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await conn.execute(create_table_sql)
                logger.info("Created database_migrations table")
    
    async def get_applied_migrations(self) -> List[str]:
        """获取已应用的迁移版本"""
        await self.create_migration_table()
        
        async with self.engine.begin() as conn:
            select_sql = text("SELECT version FROM database_migrations ORDER BY id")
            result = await conn.execute(select_sql)
            return [row[0] for row in result.fetchall()]
    
    async def mark_migration_applied(self, version: str, description: str):
        """标记迁移为已应用"""
        async with self.engine.begin() as conn:
            insert_sql = text("""
                INSERT INTO database_migrations (version, description) 
                VALUES (:version, :description)
            """)
            await conn.execute(insert_sql, {"version": version, "description": description})
    
    async def mark_migration_removed(self, version: str):
        """标记迁移为已移除"""
        async with self.engine.begin() as conn:
            delete_sql = text("DELETE FROM database_migrations WHERE version = :version")
            await conn.execute(delete_sql, {"version": version})
    
    async def upgrade(self):
        """执行数据库升级"""
        logger.info("Starting database upgrade...")
        
        applied_migrations = await self.get_applied_migrations()
        
        for version, description, upgrade_func in UPGRADE_SCRIPTS:
            if version not in applied_migrations:
                try:
                    logger.info(f"Applying migration {version}: {description}")
                    await upgrade_func(self.engine)
                    await self.mark_migration_applied(version, description)
                    logger.info(f"Successfully applied migration {version}")
                except Exception as e:
                    logger.error(f"Failed to apply migration {version}: {e}")
                    raise
            else:
                logger.info(f"Migration {version} already applied, skipping")
        
        logger.info("Database upgrade completed")
    
    async def downgrade(self, target_version: str = None):
        """执行数据库降级"""
        logger.info("Starting database downgrade...")
        
        applied_migrations = await self.get_applied_migrations()
        
        # 如果没有指定目标版本，则降级到上一个版本
        if target_version is None:
            if applied_migrations:
                target_version = applied_migrations[-1]
            else:
                logger.info("No migrations to downgrade")
                return
        
        # 按逆序执行降级脚本
        for version, description, downgrade_func in reversed(DOWNGRADE_SCRIPTS):
            if version in applied_migrations and version > target_version:
                try:
                    logger.info(f"Downgrading migration {version}: {description}")
                    await downgrade_func(self.engine)
                    await self.mark_migration_removed(version)
                    logger.info(f"Successfully downgraded migration {version}")
                except Exception as e:
                    logger.error(f"Failed to downgrade migration {version}: {e}")
                    raise
        
        logger.info("Database downgrade completed")
    
    async def get_migration_status(self):
        """获取迁移状态"""
        applied_migrations = await self.get_applied_migrations()
        
        status = {
            "applied_migrations": applied_migrations,
            "available_migrations": [version for version, _, _ in UPGRADE_SCRIPTS],
            "pending_migrations": [version for version, _, _ in UPGRADE_SCRIPTS if version not in applied_migrations]
        }
        
        return status