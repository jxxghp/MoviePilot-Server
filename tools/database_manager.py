#!/usr/bin/env python3
"""
数据库管理工具脚本
提供数据库操作的命令行接口
"""
import argparse
import asyncio
import logging
from tools.database import DatabaseMigrator, DatabaseUtils

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description='数据库管理工具')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # 迁移相关命令
    migrate_parser = subparsers.add_parser('migrate', help='数据库迁移操作')
    migrate_parser.add_argument('action', choices=['upgrade', 'downgrade', 'status', 'create'], 
                               help='迁移操作: upgrade(升级), downgrade(降级), status(状态), create(创建)')
    migrate_parser.add_argument('--revision', '-r', help='目标版本 (用于 downgrade 操作)')
    migrate_parser.add_argument('--message', '-m', help='迁移消息 (用于 create 操作)')
    
    # 数据库工具命令
    utils_parser = subparsers.add_parser('utils', help='数据库工具操作')
    utils_parser.add_argument('action', choices=['health', 'tables', 'size', 'optimize', 'backup'], 
                             help='工具操作: health(健康检查), tables(表列表), size(大小), optimize(优化), backup(备份)')
    utils_parser.add_argument('--table', '-t', help='表名 (用于特定表操作)')
    utils_parser.add_argument('--backup-name', help='备份表名 (用于备份操作)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        if args.command == 'migrate':
            migrator = DatabaseMigrator()
            
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
                    
            elif args.action == 'create':
                if not args.message:
                    print("错误: 创建迁移需要提供消息 (-m 参数)")
                    return 1
                logger.info(f"创建新迁移: {args.message}")
                migrator.create_migration(args.message)
                logger.info("迁移创建完成")
        
        elif args.command == 'utils':
            if args.action == 'health':
                health = await DatabaseUtils.health_check()
                print("\n=== 数据库健康检查 ===")
                print(f"状态: {health['status']}")
                print(f"连接: {'正常' if health['connection'] else '异常'}")
                if health['connection']:
                    print(f"表数量: {health['tables_count']}")
                    print(f"数据库大小: {health['database_size']}")
                    print(f"引擎类型: {health['engine_type']}")
                else:
                    print(f"错误: {health['error']}")
            
            elif args.action == 'tables':
                tables = await DatabaseUtils.get_all_tables()
                print("\n=== 数据库表列表 ===")
                for i, table in enumerate(tables, 1):
                    count = await DatabaseUtils.get_table_count(table)
                    print(f"{i:2d}. {table} ({count} 条记录)")
            
            elif args.action == 'size':
                size_info = await DatabaseUtils.get_database_size()
                print("\n=== 数据库大小信息 ===")
                for key, value in size_info.items():
                    print(f"{key}: {value}")
            
            elif args.action == 'optimize':
                logger.info("开始优化数据库...")
                await DatabaseUtils.optimize_database()
                logger.info("数据库优化完成")
            
            elif args.action == 'backup':
                if not args.table:
                    print("错误: 备份操作需要指定表名 (-t 参数)")
                    return 1
                
                logger.info(f"开始备份表: {args.table}")
                backup_name = await DatabaseUtils.backup_table(args.table, args.backup_name)
                logger.info(f"表备份完成: {backup_name}")
                print(f"备份表名: {backup_name}")
    
    except Exception as e:
        logger.error(f"操作失败: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    exit(exit_code)