# 数据库升级功能说明

## 概述

本项目实现了自动数据库升级功能，可以在应用启动时自动执行数据库结构升级，确保数据库结构与代码模型保持一致。

## 功能特性

- **自动升级**: 应用启动时自动检查并执行待执行的数据库升级
- **版本管理**: 通过版本号管理数据库升级脚本
- **回滚支持**: 支持数据库降级操作
- **状态查询**: 可以查看当前数据库迁移状态
- **多数据库支持**: 支持 PostgreSQL 和 SQLite 数据库

## 文件结构

```
app/db/migrations/
├── __init__.py                    # 迁移模块初始化
├── upgrader.py                    # 数据库升级管理器
└── 001_add_genre_ids.py           # 升级脚本示例
```

## 升级脚本

### 当前升级脚本

- **001**: 为 `subscribe_statistics` 表添加 `genre_ids` 字段

### 添加新的升级脚本

1. 在 `app/db/migrations/` 目录下创建新的升级脚本文件
2. 按照命名规范: `{版本号}_{描述}.py`
3. 在脚本中定义升级和降级函数
4. 将脚本添加到 `UPGRADE_SCRIPTS` 和 `DOWNGRADE_SCRIPTS` 列表中

### 升级脚本模板

```python
"""
数据库升级脚本 - {描述}
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
import logging

logger = logging.getLogger(__name__)


async def upgrade_{版本号}_{描述}(engine: AsyncEngine):
    """
    升级脚本 {版本号}: {描述}
    
    Args:
        engine: 数据库引擎
    """
    try:
        async with engine.begin() as conn:
            # 执行升级操作
            # 例如: 添加字段、创建表、修改索引等
            pass
        logger.info("升级成功")
    except Exception as e:
        logger.error(f"升级失败: {e}")
        raise


async def downgrade_{版本号}_{描述}(engine: AsyncEngine):
    """
    降级脚本 {版本号}: {描述}
    
    Args:
        engine: 数据库引擎
    """
    try:
        async with engine.begin() as conn:
            # 执行降级操作
            # 例如: 删除字段、删除表、修改索引等
            pass
        logger.info("降级成功")
    except Exception as e:
        logger.error(f"降级失败: {e}")
        raise


# 将脚本添加到列表中
UPGRADE_SCRIPTS.append(("{版本号}", "{描述}", upgrade_{版本号}_{描述}))
DOWNGRADE_SCRIPTS.append(("{版本号}", "{描述}", downgrade_{版本号}_{描述}))
```

## 使用方法

### 自动升级

应用启动时会自动执行数据库升级，无需手动操作。

### 手动管理

使用 `manage_db.py` 脚本进行手动数据库管理：

```bash
# 查看数据库迁移状态
python manage_db.py status

# 执行数据库升级
python manage_db.py upgrade

# 执行数据库降级到指定版本
python manage_db.py downgrade --target-version 001

# 执行数据库降级到上一个版本
python manage_db.py downgrade
```

## 数据库迁移记录

系统会在数据库中创建 `database_migrations` 表来记录已执行的迁移：

```sql
CREATE TABLE database_migrations (
    id INTEGER PRIMARY KEY,
    version VARCHAR(50) NOT NULL UNIQUE,
    description VARCHAR(255),
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 注意事项

1. **备份数据**: 在执行升级前建议备份数据库
2. **版本顺序**: 升级脚本按版本号顺序执行
3. **事务安全**: 每个升级脚本都在事务中执行，失败时会回滚
4. **字段检查**: 升级脚本会检查字段是否已存在，避免重复操作
5. **多数据库支持**: 脚本会自动检测数据库类型并使用相应的SQL语法

## 错误处理

如果升级过程中出现错误：

1. 检查日志输出，了解具体错误信息
2. 确认数据库连接正常
3. 检查升级脚本语法是否正确
4. 必要时可以手动修复数据库结构

## 示例：添加新字段

以下是为 `subscribe_statistics` 表添加 `genre_ids` 字段的完整示例：

```python
async def upgrade_001_add_genre_ids_to_subscribe_statistics(engine: AsyncEngine):
    """为 subscribe_statistics 表添加 genre_ids 字段"""
    try:
        async with engine.begin() as conn:
            # 检查字段是否已存在
            if engine.url.drivername == "postgresql+asyncpg":
                check_column_sql = text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'subscribe_statistics' 
                    AND column_name = 'genre_ids'
                """)
            else:
                check_column_sql = text("""
                    SELECT name 
                    FROM pragma_table_info('subscribe_statistics') 
                    WHERE name = 'genre_ids'
                """)
            
            result = await conn.execute(check_column_sql)
            column_exists = result.fetchone() is not None
            
            if not column_exists:
                # 添加字段
                add_column_sql = text("""
                    ALTER TABLE subscribe_statistics 
                    ADD COLUMN genre_ids VARCHAR
                """)
                await conn.execute(add_column_sql)
                logger.info("Successfully added genre_ids column")
            else:
                logger.info("genre_ids column already exists")
                
    except Exception as e:
        logger.error(f"Failed to add genre_ids column: {e}")
        raise
```