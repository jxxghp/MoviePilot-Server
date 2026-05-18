# MoviePilot Server 数据库配置说明

## 数据库支持

本项目现在支持两种数据库类型：
- **SQLite** (默认)
- **PostgreSQL**

共享媒体识别缓存使用 **Redis** 存储，与主数据库类型无关，需要单独配置。

## 环境变量配置

### 数据库类型切换

通过设置 `DATABASE_TYPE` 环境变量来切换数据库类型：

```bash
# 使用 SQLite (默认)
DATABASE_TYPE=sqlite

# 使用 PostgreSQL
DATABASE_TYPE=postgresql
```

### SQLite 配置

当使用 SQLite 时，只需要配置：

```bash
DATABASE_TYPE=sqlite
CONFIG_DIR=.  # 数据库文件存储目录
```

### PostgreSQL 配置

当使用 PostgreSQL 时，需要配置以下环境变量：

```bash
DATABASE_TYPE=postgresql
DB_HOST=localhost      # PostgreSQL 服务器地址
DB_PORT=5432          # PostgreSQL 端口
DB_NAME=moviepilot    # 数据库名称
DB_USER=postgres      # 数据库用户名
DB_PASSWORD=postgres  # 数据库密码
```

连接池默认按每个 worker 独立创建，4 个 worker 时默认最多使用 `4 * (DB_POOL_SIZE + DB_MAX_OVERFLOW)` 个 PostgreSQL 连接。当前默认值适合 4 核机器上几千个客户端连接的常见 API 场景，总上限为 80 个数据库连接：

```bash
DB_POOL_SIZE=15      # 每个 worker 常驻连接数
DB_MAX_OVERFLOW=5    # 每个 worker 临时溢出连接数
DB_POOL_TIMEOUT=180
DB_POOL_RECYCLE=3600
```

## 服务启动配置

生产环境默认会按当前运行环境可见 CPU 核心数启动多个 Uvicorn worker，最多自动使用 4 个 worker。`DEBUG=true` 时固定为 1 个 worker，避免热重载和多进程冲突。

```bash
SERVER_WORKERS=4     # 优先使用
WEB_CONCURRENCY=4    # 兼容常见部署平台变量
SERVER_BACKLOG=4096
SERVER_LIMIT_CONCURRENCY=0
SERVER_TIMEOUT_KEEP_ALIVE=5
```

如果 PostgreSQL 的 `max_connections` 较小，需要同时降低 `SERVER_WORKERS`、`DB_POOL_SIZE` 或 `DB_MAX_OVERFLOW`。
`SERVER_LIMIT_CONCURRENCY=0` 表示不主动限制单 worker 并发；接口延迟升高或数据库连接等待明显时，可以设置为 500-1000 做应用层排队保护。

## Docker 部署

### 使用 PostgreSQL

使用提供的 `docker-compose.yml` 文件，其中已配置了 PostgreSQL 服务：

```bash
docker-compose up -d
```

这将启动：
- PostgreSQL 数据库服务
- MoviePilot Server 应用服务

### 使用 SQLite

如果只想使用 SQLite，可以修改 `docker-compose.yml` 中的环境变量：

```yaml
moviepilot-server:
  environment:
    - DATABASE_TYPE=sqlite
    - CONFIG_DIR=/config
```

## Redis 配置

共享媒体识别接口 `/recognize/share` 依赖 Redis 存储海量缓存数据。

### 最简配置

```bash
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=your-strong-password
```

### 完整配置

```bash
REDIS_URL=
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_USERNAME=
REDIS_PASSWORD=
REDIS_SSL=false
REDIS_MAX_CONNECTIONS=50
REDIS_CONNECT_TIMEOUT=5
REDIS_SOCKET_TIMEOUT=5
REDIS_KEY_PREFIX=moviepilot
```

说明：
- 优先使用 `REDIS_URL`，设置后会覆盖 `REDIS_HOST`/`REDIS_PORT` 等拆分配置。
- Redis 启用密码后，需要同时为服务端和应用端配置同一个 `REDIS_PASSWORD`。
- 共享识别缓存键会写入 `${REDIS_KEY_PREFIX}:media_recognize_share:*` 命名空间。
- 默认 `docker/docker-compose.yml` 已开启 AOF + RDB 持久化，并将数据目录挂载到 `/root/redis`。
- 默认 `docker/docker-compose.yml` 已设置 `maxmemory 10gb`，淘汰策略为 `allkeys-lru`。
- 默认 `docker/docker-compose.yml` 会使用 `REDIS_PASSWORD` 环境变量；未设置时会回退到示例密码 `moviepilot_redis_password`，建议部署时覆盖。
- 应用启动时会初始化 Redis，连接失败会导致启动失败。
