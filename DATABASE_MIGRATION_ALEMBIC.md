# 数据库迁移功能说明 - 基于 Alembic

## 概述

参考 [MoviePilot 项目](https://github.com/jxxghp/MoviePilot/tree/v2/database) 的实现方式，使用 Alembic 进行数据库迁移管理。Alembic 是 SQLAlchemy 官方推荐的数据库迁移工具，提供了完整的版本控制和迁移管理功能。

## 实现方案

### 核心组件

1. **Alembic 配置**: `alembic.ini` 和 `alembic/env.py`
2. **迁移脚本**: `alembic/versions/` 目录下的版本化迁移文件
3. **迁移管理器**: `app/db/migrator.py` - 封装 Alembic 操作
4. **自动迁移**: `main.py` - 启动时自动执行迁移

### 文件结构

```
alembic/
├── env.py                    # Alembic 环境配置
└── versions/
    └── 0001_add_genre_ids.py # 迁移脚本

app/db/
└── migrator.py              # 数据库迁移管理器

alembic.ini                  # Alembic 配置文件
manage_db.py                 # 数据库管理命令工具
test_alembic_migration.py    # 测试脚本
```

## 核心功能

### 1. 自动迁移

应用启动时自动检查并执行数据库迁移：

```python
# main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 执行数据库迁移
    migrator = DatabaseMigrator()
    migrator.upgrade()
    
    yield
    await engine.dispose()
```

### 2. 迁移管理器

`DatabaseMigrator` 类封装了 Alembic 的核心功能：

```python
class DatabaseMigrator:
    def upgrade(self):
        """升级数据库到最新版本"""
        
    def downgrade(self, revision="base"):
        """降级数据库到指定版本"""
        
    def get_migration_status(self):
        """获取迁移状态信息"""
```

### 3. 版本化迁移

每个迁移都有唯一的版本号，支持升级和降级：

```python
# alembic/versions/0001_add_genre_ids.py
def upgrade() -> None:
    """添加 genre_ids 字段"""
    op.add_column('subscribe_statistics', 
                  sa.Column('genre_ids', sa.String(), nullable=True))

def downgrade() -> None:
    """移除 genre_ids 字段"""
    op.drop_column('subscribe_statistics', 'genre_ids')
```

## 使用方法

### 自动迁移

应用启动时会自动执行迁移，无需手动操作。

### 手动管理

使用 `manage_db.py` 脚本进行手动管理：

```bash
# 查看迁移状态
python manage_db.py status

# 执行数据库升级
python manage_db.py upgrade

# 降级到指定版本
python manage_db.py downgrade --revision 0001

# 降级到初始状态
python manage_db.py downgrade

# 查看迁移历史
python manage_db.py history
```

### Alembic 原生命令

也可以直接使用 Alembic 命令：

```bash
# 查看当前状态
alembic current

# 查看迁移历史
alembic history

# 升级到最新版本
alembic upgrade head

# 降级到指定版本
alembic downgrade 0001

# 生成新的迁移脚本
alembic revision --autogenerate -m "描述信息"
```

## 添加新的迁移

### 1. 自动生成迁移脚本

```bash
alembic revision --autogenerate -m "Add new field to table"
```

### 2. 手动创建迁移脚本

在 `alembic/versions/` 目录下创建新的迁移文件：

```python
"""Add new field

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade() -> None:
    """添加新字段"""
    op.add_column('table_name', sa.Column('new_field', sa.String(), nullable=True))

def downgrade() -> None:
    """移除新字段"""
    op.drop_column('table_name', 'new_field')
```

## 优势

1. **版本控制**: 完整的迁移历史记录
2. **回滚支持**: 支持降级到任意历史版本
3. **自动检测**: 可以自动检测模型变化并生成迁移脚本
4. **事务安全**: 每个迁移都在事务中执行
5. **多数据库支持**: 支持 PostgreSQL、SQLite、MySQL 等
6. **生产就绪**: 被广泛使用，稳定可靠

## 当前实现

- ✅ 为 `subscribe_statistics` 表添加了 `genre_ids` 字段
- ✅ 在 `SubscribeStatisticItem` 模型中添加了对应字段
- ✅ 实现了基于 Alembic 的数据库迁移
- ✅ 支持启动时自动迁移
- ✅ 提供了完整的迁移管理工具
- ✅ 支持 PostgreSQL 和 SQLite 数据库

## 测试

使用测试脚本验证功能：

```bash
python test_alembic_migration.py
```

## 注意事项

1. **备份数据**: 在生产环境中执行迁移前建议备份数据
2. **版本顺序**: 迁移脚本按版本号顺序执行
3. **字段检查**: 迁移脚本会检查字段是否存在，避免重复操作
4. **错误处理**: 迁移失败时会回滚事务
5. **并发安全**: Alembic 使用锁机制确保并发安全

## 参考

- [Alembic 官方文档](https://alembic.sqlalchemy.org/)
- [MoviePilot 项目数据库实现](https://github.com/jxxghp/MoviePilot/tree/v2/database)
- [SQLAlchemy 迁移最佳实践](https://docs.sqlalchemy.org/en/20/orm/extensions/alembic.html)