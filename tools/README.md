# 数据库工具目录

这个目录包含了项目中所有数据库相关的工具和辅助功能。

## 目录结构

```
tools/
├── __init__.py                 # 工具模块入口
├── database/                   # 数据库工具目录
│   ├── __init__.py            # 数据库工具模块入口
│   ├── connection.py          # 数据库连接管理
│   ├── session.py             # 数据库会话管理
│   ├── migrator.py            # 数据库迁移工具
│   └── utils.py               # 数据库工具集合
└── database_manager.py        # 数据库管理命令行工具
```

## 主要功能

### 1. 数据库连接管理 (`connection.py`)
- `DatabaseConnection`: 数据库连接管理类
- 支持 PostgreSQL 和 SQLite
- 自动配置连接池参数
- 提供全局连接实例

### 2. 数据库会话管理 (`session.py`)
- `DatabaseSessionManager`: 会话管理器
- `get_db_session()`: 获取数据库会话的依赖注入函数
- 支持异步上下文管理器

### 3. 数据库迁移工具 (`migrator.py`)
- `DatabaseMigrator`: 基于 Alembic 的迁移管理器
- 支持升级、降级、状态检查
- 提供迁移历史查询

### 4. 数据库工具集合 (`utils.py`)
- `DatabaseUtils`: 数据库操作工具类
- 提供表信息查询、数据备份、健康检查等功能
- 支持原始SQL执行

## 使用方法

### 命令行工具

#### 1. 传统迁移管理 (manage_db.py)
```bash
# 升级数据库
python manage_db.py upgrade

# 降级数据库
python manage_db.py downgrade --revision base

# 查看迁移状态
python manage_db.py status

# 查看迁移历史
python manage_db.py history
```

#### 2. 新的数据库管理工具 (database_manager.py)
```bash
# 迁移操作
python tools/database_manager.py migrate upgrade
python tools/database_manager.py migrate downgrade --revision base
python tools/database_manager.py migrate status
python tools/database_manager.py migrate create --message "添加新字段"

# 数据库工具操作
python tools/database_manager.py utils health
python tools/database_manager.py utils tables
python tools/database_manager.py utils size
python tools/database_manager.py utils optimize
python tools/database_manager.py utils backup --table users
```

### 代码中使用

#### 1. 导入数据库工具
```python
from tools.database import DatabaseConnection, DatabaseMigrator, DatabaseUtils
from tools.database import get_db_session, DatabaseSessionManager
```

#### 2. 使用数据库连接
```python
from tools.database import db_connection

# 获取引擎
engine = db_connection.engine

# 获取会话工厂
session_factory = db_connection.session_factory
```

#### 3. 使用会话管理
```python
from tools.database import DatabaseSessionManager

async with DatabaseSessionManager() as session:
    # 使用 session 进行数据库操作
    result = await session.execute(text("SELECT 1"))
```

#### 4. 使用数据库工具
```python
from tools.database import DatabaseUtils

# 健康检查
health = await DatabaseUtils.health_check()

# 获取所有表
tables = await DatabaseUtils.get_all_tables()

# 执行原始SQL
result = await DatabaseUtils.execute_raw_sql("SELECT COUNT(*) FROM users")
```

## 迁移说明

原有的数据库相关文件已移动到工具目录中：

- `app/db/database.py` → `tools/database/connection.py`
- `app/db/migrator.py` → `tools/database/migrator.py`
- `app/db/deps.py` → 更新导入路径

为了保持向后兼容性，原有的 `manage_db.py` 仍然可用，但建议使用新的 `tools/database_manager.py` 工具。

## 特性

- ✅ 支持 PostgreSQL 和 SQLite
- ✅ 异步数据库操作
- ✅ 连接池管理
- ✅ 数据库迁移管理
- ✅ 健康检查
- ✅ 数据备份
- ✅ 表信息查询
- ✅ 数据库优化
- ✅ 命令行工具
- ✅ 向后兼容