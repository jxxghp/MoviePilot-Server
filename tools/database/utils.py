"""
数据库工具集合
提供常用的数据库操作辅助函数
"""
import asyncio
from typing import Any, Dict, List, Optional, Type, TypeVar
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from .connection import db_connection
from .session import DatabaseSessionManager

T = TypeVar('T', bound=DeclarativeBase)


class DatabaseUtils:
    """数据库工具类"""
    
    @staticmethod
    async def execute_raw_sql(sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        执行原始SQL语句
        
        Args:
            sql: SQL语句
            params: 参数字典
            
        Returns:
            查询结果
        """
        async with DatabaseSessionManager() as session:
            result = await session.execute(text(sql), params or {})
            await session.commit()
            return result
    
    @staticmethod
    async def get_table_info(table_name: str) -> List[Dict[str, Any]]:
        """
        获取表结构信息
        
        Args:
            table_name: 表名
            
        Returns:
            表结构信息列表
        """
        if db_connection.engine.url.drivername == 'postgresql':
            sql = """
            SELECT 
                column_name,
                data_type,
                is_nullable,
                column_default,
                character_maximum_length
            FROM information_schema.columns 
            WHERE table_name = :table_name
            ORDER BY ordinal_position
            """
        else:  # SQLite
            sql = "PRAGMA table_info(:table_name)"
        
        result = await DatabaseUtils.execute_raw_sql(sql, {"table_name": table_name})
        
        if db_connection.engine.url.drivername == 'postgresql':
            return [dict(row._mapping) for row in result]
        else:  # SQLite
            return [dict(row._mapping) for row in result]
    
    @staticmethod
    async def get_table_count(table_name: str) -> int:
        """
        获取表的记录数
        
        Args:
            table_name: 表名
            
        Returns:
            记录数
        """
        sql = f"SELECT COUNT(*) as count FROM {table_name}"
        result = await DatabaseUtils.execute_raw_sql(sql)
        return result.scalar()
    
    @staticmethod
    async def check_table_exists(table_name: str) -> bool:
        """
        检查表是否存在
        
        Args:
            table_name: 表名
            
        Returns:
            表是否存在
        """
        if db_connection.engine.url.drivername == 'postgresql':
            sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = :table_name
            )
            """
        else:  # SQLite
            sql = """
            SELECT EXISTS (
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name=:table_name
            )
            """
        
        result = await DatabaseUtils.execute_raw_sql(sql, {"table_name": table_name})
        return result.scalar()
    
    @staticmethod
    async def get_all_tables() -> List[str]:
        """
        获取所有表名
        
        Returns:
            表名列表
        """
        if db_connection.engine.url.drivername == 'postgresql':
            sql = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        else:  # SQLite
            sql = """
            SELECT name 
            FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        
        result = await DatabaseUtils.execute_raw_sql(sql)
        return [row[0] for row in result]
    
    @staticmethod
    async def backup_table(table_name: str, backup_name: Optional[str] = None) -> str:
        """
        备份表数据
        
        Args:
            table_name: 原表名
            backup_name: 备份表名，如果为None则自动生成
            
        Returns:
            备份表名
        """
        if backup_name is None:
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{table_name}_backup_{timestamp}"
        
        # 创建备份表
        create_sql = f"CREATE TABLE {backup_name} AS SELECT * FROM {table_name}"
        await DatabaseUtils.execute_raw_sql(create_sql)
        
        return backup_name
    
    @staticmethod
    async def truncate_table(table_name: str, cascade: bool = False):
        """
        清空表数据
        
        Args:
            table_name: 表名
            cascade: 是否级联删除（PostgreSQL）
        """
        if db_connection.engine.url.drivername == 'postgresql':
            sql = f"TRUNCATE TABLE {table_name}"
            if cascade:
                sql += " CASCADE"
        else:  # SQLite
            sql = f"DELETE FROM {table_name}"
        
        await DatabaseUtils.execute_raw_sql(sql)
    
    @staticmethod
    async def get_database_size() -> Dict[str, Any]:
        """
        获取数据库大小信息
        
        Returns:
            数据库大小信息
        """
        if db_connection.engine.url.drivername == 'postgresql':
            sql = """
            SELECT 
                pg_size_pretty(pg_database_size(current_database())) as database_size,
                pg_database_size(current_database()) as database_size_bytes
            """
        else:  # SQLite
            sql = """
            SELECT 
                page_count * page_size as database_size_bytes,
                printf('%.2f MB', (page_count * page_size) / 1024.0 / 1024.0) as database_size
            FROM pragma_page_count(), pragma_page_size()
            """
        
        result = await DatabaseUtils.execute_raw_sql(sql)
        return dict(result.fetchone()._mapping)
    
    @staticmethod
    async def optimize_database():
        """
        优化数据库（清理、重建索引等）
        """
        if db_connection.engine.url.drivername == 'postgresql':
            # PostgreSQL 优化
            await DatabaseUtils.execute_raw_sql("VACUUM ANALYZE")
        else:  # SQLite
            # SQLite 优化
            await DatabaseUtils.execute_raw_sql("VACUUM")
            await DatabaseUtils.execute_raw_sql("ANALYZE")
    
    @staticmethod
    async def health_check() -> Dict[str, Any]:
        """
        数据库健康检查
        
        Returns:
            健康检查结果
        """
        try:
            # 测试连接
            await DatabaseUtils.execute_raw_sql("SELECT 1")
            
            # 获取基本信息
            tables = await DatabaseUtils.get_all_tables()
            db_size = await DatabaseUtils.get_database_size()
            
            return {
                "status": "healthy",
                "connection": True,
                "tables_count": len(tables),
                "database_size": db_size,
                "engine_type": db_connection.engine.url.drivername
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "connection": False,
                "error": str(e)
            }