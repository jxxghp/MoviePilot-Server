#!/usr/bin/env python3
"""
数据库升级功能测试脚本 - 简化版本
"""
import asyncio
import logging
from app.db.database import engine
from app.db.upgrade import upgrade_database

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_database_upgrade():
    """测试数据库升级功能"""
    logger.info("开始测试简化的数据库升级功能...")
    
    try:
        # 测试执行升级
        await upgrade_database(engine)
        logger.info("数据库升级测试完成")
        
        logger.info("所有测试通过！")
        return True
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
        return False
    
    finally:
        await engine.dispose()


if __name__ == '__main__':
    success = asyncio.run(test_database_upgrade())
    if success:
        print("✅ 简化的数据库升级功能测试通过")
    else:
        print("❌ 简化的数据库升级功能测试失败")
        exit(1)