# FastAPI MoviePilot Server 项目结构

## 项目概述
本项目已按照FastAPI最佳实践重新组织，采用模块化架构，提高了代码的可维护性和可扩展性。

## 项目结构

```
workspace/
├── app/                          # 主应用目录
│   ├── __init__.py              # 应用初始化
│   ├── api/                     # API路由模块
│   │   ├── __init__.py
│   │   └── v1/                  # API版本1
│   │       ├── __init__.py
│   │       ├── api.py           # 路由汇总
│   │       ├── plugin.py        # 插件相关API
│   │       ├── subscribe.py     # 订阅相关API
│   │       ├── subscribe_share.py # 订阅分享API
│   │       └── workflow_share.py # 工作流分享API
│   ├── core/                    # 核心配置模块
│   │   ├── __init__.py
│   │   ├── config.py            # 应用配置
│   │   └── cache.py             # 缓存管理
│   ├── db/                      # 数据库模块
│   │   ├── __init__.py
│   │   ├── database.py          # 数据库连接
│   │   └── deps.py              # 依赖注入
│   ├── models/                  # 数据库模型
│   │   ├── __init__.py
│   │   └── database.py          # SQLAlchemy模型
│   ├── schemas/                 # Pydantic模型
│   │   ├── __init__.py
│   │   └── models.py            # 请求/响应模型
│   ├── services/                # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── plugin_service.py    # 插件服务
│   │   ├── subscribe_service.py # 订阅服务
│   │   ├── subscribe_share_service.py # 订阅分享服务
│   │   └── workflow_share_service.py # 工作流分享服务
│   └── utils/                   # 工具模块
│       └── __init__.py
├── main.py                      # 应用入口
├── requirements.txt             # 依赖包
├── Dockerfile                   # Docker配置
├── docker-compose.yml           # Docker Compose配置
├── nginx.conf                   # Nginx配置
└── README.md                    # 项目说明
```

## 架构设计

### 1. 分层架构
- **API层** (`app/api/`): 处理HTTP请求和响应
- **服务层** (`app/services/`): 业务逻辑处理
- **数据层** (`app/models/`): 数据库模型定义
- **配置层** (`app/core/`): 应用配置和缓存管理

### 2. 模块化设计
- **插件模块**: 插件安装统计相关功能
- **订阅模块**: 订阅统计相关功能
- **分享模块**: 订阅和工作流分享功能

### 3. 依赖注入
- 使用FastAPI的依赖注入系统管理数据库会话
- 统一的数据库连接和会话管理

## 主要改进

### 1. 配置管理
- 集中化的配置管理 (`app/core/config.py`)
- 环境变量支持
- 数据库类型自动检测

### 2. 缓存优化
- 统一的缓存管理器 (`app/core/cache.py`)
- 支持多种缓存类型
- 自动缓存清理

### 3. 数据库优化
- 模块化的数据库连接管理
- 支持PostgreSQL和SQLite
- 异步数据库操作

### 4. API设计
- RESTful API设计
- 版本化API (`/api/v1/`)
- 统一的错误处理

### 5. 代码组织
- 单一职责原则
- 高内聚低耦合
- 易于测试和维护

## 运行方式

### 开发环境
```bash
python main.py
```

### Docker环境
```bash
docker-compose up -d
```

## API端点

### 插件相关
- `GET /api/v1/plugin/install/{pid}` - 安装插件计数
- `POST /api/v1/plugin/install` - 批量安装插件
- `GET /api/v1/plugin/statistic` - 查询插件统计

### 订阅相关
- `POST /api/v1/subscribe/add` - 添加订阅统计
- `POST /api/v1/subscribe/done` - 完成订阅统计
- `POST /api/v1/subscribe/report` - 批量报告订阅
- `GET /api/v1/subscribe/statistic` - 查询订阅统计

### 订阅分享相关
- `POST /api/v1/subscribe/share` - 新增订阅分享
- `GET /api/v1/subscribe/shares` - 查询分享的订阅
- `GET /api/v1/subscribe/fork/{shareid}` - 复用分享的订阅
- `DELETE /api/v1/subscribe/share/{sid}` - 删除订阅分享
- `GET /api/v1/subscribe/share/statistics` - 查询分享统计

### 工作流分享相关
- `POST /api/v1/workflow/share` - 新增工作流分享
- `GET /api/v1/workflow/shares` - 查询分享的工作流
- `GET /api/v1/workflow/fork/{shareid}` - 复用分享的工作流
- `DELETE /api/v1/workflow/share/{sid}` - 删除工作流分享

## 环境变量

- `DATABASE_TYPE`: 数据库类型 (sqlite/postgresql)
- `DB_HOST`: PostgreSQL主机地址
- `DB_PORT`: PostgreSQL端口
- `DB_NAME`: 数据库名称
- `DB_USER`: 数据库用户名
- `DB_PASSWORD`: 数据库密码
- `CONFIG_DIR`: 配置文件目录
- `DEBUG`: 调试模式
- `HOST`: 服务器主机
- `PORT`: 服务器端口