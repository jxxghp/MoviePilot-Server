#!/usr/bin/env python3
"""
数据库升级管理命令脚本
"""
import asyncio
import argparse
import logging
from app.db.database import engine
from app.db.migrations.upgrader import DatabaseUpgrader

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description='数据库升级管理工具')
    parser.add_argument('action', choices=['upgrade', 'downgrade', 'status'], 
                       help='操作类型: upgrade(升级), downgrade(降级), status(状态)')
    parser.add_argument('--target-version', '-t', 
                       help='降级目标版本 (仅用于downgrade操作)')
    
    args = parser.parse_args()
    
    upgrader = DatabaseUpgrader(engine)
    
    try:
        if args.action == 'upgrade':
            logger.info("开始执行数据库升级...")
            await upgrader.upgrade()
            logger.info("数据库升级完成")
            
        elif args.action == 'downgrade':
            logger.info("开始执行数据库降级...")
            await upgrader.downgrade(args.target_version)
            logger.info("数据库降级完成")
            
        elif args.action == 'status':
            status = await upgrader.get_migration_status()
            print("\n=== 数据库迁移状态 ===")
            print(f"已应用的迁移: {status['applied_migrations']}")
            print(f"可用的迁移: {status['available_migrations']}")
            print(f"待执行的迁移: {status['pending_migrations']}")
            
    except Exception as e:
        logger.error(f"操作失败: {e}")
        return 1
    
    finally:
        await engine.dispose()
    
    return 0


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    exit(exit_code)