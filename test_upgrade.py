#!/usr/bin/env python3
"""
数据库升级功能测试脚本
"""
import asyncio
import logging
from app.db.database import engine
from app.db.migrations.upgrader import DatabaseUpgrader

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_database_upgrade():
    """测试数据库升级功能"""
    logger.info("开始测试数据库升级功能...")
    
    upgrader = DatabaseUpgrader(engine)
    
    try:
        # 测试获取迁移状态
        logger.info("测试获取迁移状态...")
        status = await upgrader.get_migration_status()
        logger.info(f"迁移状态: {status}")
        
        # 测试执行升级
        logger.info("测试执行数据库升级...")
        await upgrader.upgrade()
        logger.info("数据库升级测试完成")
        
        # 再次检查状态
        status_after = await upgrader.get_migration_status()
        logger.info(f"升级后状态: {status_after}")
        
        logger.info("所有测试通过！")
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
        return False
    
    finally:
        await engine.dispose()
    
    return True


if __name__ == '__main__':
    success = asyncio.run(test_database_upgrade())
    if success:
        print("✅ 数据库升级功能测试通过")
    else:
        print("❌ 数据库升级功能测试失败")
        exit(1)