# RailgunPT 站点配置文档

## 概述

本文档描述了为 RailgunPT 站点添加的配置，参考 13city 的配置格式。RailgunPT 是一个基于 NexusPHP 的私有种子站点。

## 站点信息

- **站点名称**: RailgunPT
- **站点URL**: https://bilibili.download/
- **站点类型**: NexusPHP
- **状态**: 启用

## 分类配置

站点包含以下5个分类，每个分类都有对应的ID、中文名称和英文名称：

| ID | 中文名称 | 英文名称 | 描述 |
|----|----------|----------|------|
| 401 | 电影 | Movies | 电影资源分类 |
| 402 | 电视剧 | TV Series | 电视剧资源分类 |
| 403 | 综艺 | TV Shows | 综艺节目资源分类 |
| 404 | 纪录片 | Documentaries | 纪录片资源分类 |
| 405 | 动漫 | Animations | 动漫资源分类 |

## NexusPHP 配置

RailgunPT 使用 NexusPHP 系统，配置了以下参数：

- **API端点**: `/api.php`
- **RSS端点**: `/rss.php`
- **搜索端点**: `/torrents.php`
- **用户代理**: `RailgunPT-Client/1.0`
- **超时时间**: 30秒
- **重试次数**: 3次

## 配置文件

### 1. Pydantic 模型配置 (`app/core/site_config.py`)

使用 Pydantic 模型定义站点配置，提供类型安全和验证：

```python
class SiteCategory(BaseModel):
    id: int
    name: str
    name_en: str

class SiteConfig(BaseModel):
    name: str
    url: str
    type: str
    categories: List[SiteCategory]
    enabled: bool = True
    description: Optional[str] = None
```

### 2. 字典配置 (`app/core/railgunpt_config.py`)

提供简单的字典格式配置，便于直接使用：

```python
RAILGUNPT_SITE_CONFIG = {
    "name": "RailgunPT",
    "url": "https://bilibili.download/",
    "type": "NexusPHP",
    "categories": {
        401: {"id": 401, "name": "电影", "name_en": "Movies"},
        # ... 其他分类
    }
}
```

### 3. API 端点 (`app/api/site_config.py`)

提供 RESTful API 接口访问站点配置：

- `GET /api/sites/` - 获取所有站点配置
- `GET /api/sites/{site_name}` - 获取指定站点配置
- `GET /api/sites/{site_name}/categories` - 获取站点分类

## 使用方法

### 获取站点配置

```python
from app.core.site_config import get_site_config

# 获取 RailgunPT 配置
config = get_site_config("railgunpt")
if config:
    print(f"站点名称: {config.name}")
    print(f"站点URL: {config.url}")
```

### 获取站点分类

```python
from app.core.site_config import get_site_categories

# 获取 RailgunPT 分类
categories = get_site_categories("railgunpt")
for category in categories:
    print(f"{category.id}: {category.name} ({category.name_en})")
```

### API 调用示例

```bash
# 获取所有站点配置
curl http://localhost:3001/api/sites/

# 获取 RailgunPT 配置
curl http://localhost:3001/api/sites/railgunpt

# 获取 RailgunPT 分类
curl http://localhost:3001/api/sites/railgunpt/categories
```

## 扩展说明

### 添加新站点

要添加新的站点配置，可以：

1. 在 `app/core/site_config.py` 中添加新的 `SiteConfig` 实例
2. 在 `SITE_CONFIGS` 字典中注册新站点
3. 在 `app/core/railgunpt_config.py` 中添加对应的字典配置（可选）

### 修改分类

要修改现有分类或添加新分类：

1. 更新 `SiteConfig` 中的 `categories` 列表
2. 更新字典配置中的 `categories` 字段
3. 确保分类ID的唯一性

## 测试

运行测试脚本验证配置：

```bash
python3 simple_test.py
```

测试将验证：
- 配置结构的完整性
- 必需字段的存在
- 分类ID的正确性
- NexusPHP 配置的有效性

## 注意事项

1. 确保站点URL的有效性
2. 分类ID必须唯一且符合站点要求
3. NexusPHP 配置参数需要根据实际站点调整
4. API 端点需要确保服务器正在运行
5. 配置修改后需要重启服务生效