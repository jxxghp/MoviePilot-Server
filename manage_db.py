#!/usr/bin/env python3
"""
数据库迁移管理命令脚本 - 基于 Alembic
"""
import argparse
import logging
from tools.database import DatabaseMigrator

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='数据库迁移管理工具 (基于 Alembic)')
    parser.add_argument('action', choices=['upgrade', 'downgrade', 'status', 'history'], 
                       help='操作类型: upgrade(升级), downgrade(降级), status(状态), history(历史)')
    parser.add_argument('--revision', '-r', 
                       help='目标版本 (用于 downgrade 操作)')
    
    args = parser.parse_args()
    
    migrator = DatabaseMigrator()
    
    try:
        if args.action == 'upgrade':
            logger.info("开始执行数据库升级...")
            migrator.upgrade()
            logger.info("数据库升级完成")
            
        elif args.action == 'downgrade':
            target_revision = args.revision or "base"
            logger.info(f"开始执行数据库降级到版本: {target_revision}")
            migrator.downgrade(target_revision)
            logger.info("数据库降级完成")
            
        elif args.action == 'status':
            status = migrator.get_migration_status()
            print("\n=== 数据库迁移状态 ===")
            print(f"当前版本: {status['current_revision']}")
            print(f"最新版本: {status['head_revision']}")
            print(f"是否最新: {'是' if status['is_up_to_date'] else '否'}")
            print(f"有待执行迁移: {'是' if status['pending_migrations'] else '否'}")
            if 'error' in status:
                print(f"错误: {status['error']}")
                
        elif args.action == 'history':
            print("\n=== 迁移历史 ===")
            print("使用以下命令查看详细历史:")
            print("alembic history")
            print("alembic history --verbose")
            
    except Exception as e:
        logger.error(f"操作失败: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    exit_code = main()
    exit(exit_code)