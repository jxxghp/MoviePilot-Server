#!/usr/bin/env python3
"""
数据库迁移功能测试脚本 - 基于 Alembic
"""
import logging
from app.db.migrator import DatabaseMigrator

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_database_migration():
    """测试数据库迁移功能"""
    logger.info("开始测试基于 Alembic 的数据库迁移功能...")
    
    migrator = DatabaseMigrator()
    
    try:
        # 测试获取迁移状态
        logger.info("测试获取迁移状态...")
        status = migrator.get_migration_status()
        logger.info(f"迁移状态: {status}")
        
        # 测试执行升级
        logger.info("测试执行数据库升级...")
        migrator.upgrade()
        logger.info("数据库升级测试完成")
        
        # 再次检查状态
        status_after = migrator.get_migration_status()
        logger.info(f"升级后状态: {status_after}")
        
        logger.info("所有测试通过！")
        return True
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
        return False


if __name__ == '__main__':
    success = test_database_migration()
    if success:
        print("✅ 基于 Alembic 的数据库迁移功能测试通过")
    else:
        print("❌ 基于 Alembic 的数据库迁移功能测试失败")
        exit(1)