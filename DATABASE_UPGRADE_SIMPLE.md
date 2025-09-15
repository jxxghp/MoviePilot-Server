# 数据库升级功能说明 - 简化版本

## 概述

使用 SQLAlchemy 的简单方案实现数据库升级，在应用启动时自动检查并添加缺失的列。

## 实现方案

### 核心思想

1. **SQLAlchemy 自动建表**: 使用 `Base.metadata.create_all()` 创建所有表
2. **检查缺失列**: 使用 SQLAlchemy 的 `inspect` 功能检查表结构
3. **添加缺失列**: 对缺失的列执行 `ALTER TABLE` 语句

### 文件结构

```
app/db/
├── database.py          # 数据库连接
├── deps.py             # 数据库依赖
└── upgrade.py          # 数据库升级工具
```

## 核心代码

### 升级工具 (`app/db/upgrade.py`)

```python
async def add_missing_columns(engine: AsyncEngine):
    """检查并添加缺失的列"""
    async with engine.begin() as conn:
        inspector = inspect(engine.sync_engine)
        
        if 'subscribe_statistics' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('subscribe_statistics')]
            
            if 'genre_ids' not in columns:
                add_column_sql = text("ALTER TABLE subscribe_statistics ADD COLUMN genre_ids VARCHAR")
                await conn.execute(add_column_sql)
```

### 启动时自动升级 (`main.py`)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 添加缺失的列
    await upgrade_database(engine)
    
    yield
    await engine.dispose()
```

## 优势

1. **简单直接**: 不需要复杂的版本管理系统
2. **自动检测**: 使用 SQLAlchemy 的 inspect 功能自动检测表结构
3. **安全可靠**: 先检查列是否存在，再决定是否添加
4. **易于维护**: 代码简洁，容易理解和修改

## 使用方法

### 添加新列

1. 在模型中添加新字段
2. 在 `upgrade.py` 的 `add_missing_columns` 函数中添加检查逻辑
3. 重启应用，系统会自动添加缺失的列

### 示例：添加新字段

```python
# 1. 在模型中添加字段
class SubscribeStatistics(Base):
    # ... 其他字段
    new_field = Column(String)  # 新字段

# 2. 在 upgrade.py 中添加检查逻辑
if 'subscribe_statistics' in inspector.get_table_names():
    columns = [col['name'] for col in inspector.get_columns('subscribe_statistics')]
    
    if 'new_field' not in columns:
        add_column_sql = text("ALTER TABLE subscribe_statistics ADD COLUMN new_field VARCHAR")
        await conn.execute(add_column_sql)
```

## 测试

使用测试脚本验证功能：

```bash
python test_simple_upgrade.py
```

## 注意事项

1. **字段类型**: 确保 ALTER TABLE 语句中的字段类型与模型定义一致
2. **数据库兼容**: 代码同时支持 PostgreSQL 和 SQLite
3. **错误处理**: 升级过程中的错误会被记录并抛出
4. **事务安全**: 所有操作都在事务中执行

## 当前实现

- ✅ 为 `subscribe_statistics` 表添加了 `genre_ids` 字段
- ✅ 在 `SubscribeStatisticItem` 模型中添加了对应字段
- ✅ 实现了启动时自动升级功能
- ✅ 支持 PostgreSQL 和 SQLite 数据库