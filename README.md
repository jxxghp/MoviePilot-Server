# MoviePilot-Server

MoviePilot 的中心化服务端，承载统计、共享、公共服务及可选的私有消息中转。项目基于 FastAPI、SQLAlchemy 和 Redis，支持 SQLite 与 PostgreSQL，并提供 `linux/amd64`、`linux/arm64` 多架构容器镜像。

## 功能

- 插件安装次数统计
- 插件评分明细、平均分与评分人数汇总
- 订阅与工作流分享
- MoviePilot 安装版本统计
- 共享媒体识别缓存
- 115 OAuth2 授权
- MoviePilot 用户权限查询
- App 私有通知中转：APNs 与主屏幕 Web Push

## 容器镜像

每次 `main` 分支的服务端代码更新都会构建并发布统一的 `latest` 标签：

- Docker Hub：`jxxghp/moviepilot-server:latest`
- GitHub Container Registry：`ghcr.io/jxxghp/moviepilot-server:latest`

生产环境不要使用临时或本地自定义标签，以确保 `docker pull` 能获取当前正式版本。

## 快速启动

SQLite 适合本地开发或轻量部署：

```bash
docker run -d \
  --name moviepilot-server \
  --restart always \
  -p 3001:3001 \
  -e DATABASE_TYPE=sqlite \
  -e CONFIG_DIR=/config \
  -v /path/to/moviepilot-server:/config \
  jxxghp/moviepilot-server:latest
```

也可以将最后一行替换为 GitHub Packages 镜像：

```text
ghcr.io/jxxghp/moviepilot-server:latest
```

PostgreSQL、Redis 和服务端的完整编排示例见 [docker/docker-compose.yml](docker/docker-compose.yml)。数据库、连接池、Worker 与 Redis 参数说明见 [数据库配置文档](docs/DATABASE_CONFIG.md)。

应用启动时会自动检查并创建缺失的数据表。默认监听 `0.0.0.0:3001`。

## 常用配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_TYPE` | `sqlite` | `sqlite` 或 `postgresql` |
| `CONFIG_DIR` | `.` | SQLite 数据库和运行配置目录 |
| `DB_HOST` | `localhost` | PostgreSQL 地址 |
| `DB_PORT` | `5432` | PostgreSQL 端口 |
| `DB_NAME` | `moviepilot` | PostgreSQL 数据库名 |
| `DB_USER` | `postgres` | PostgreSQL 用户名 |
| `DB_PASSWORD` | `postgres` | PostgreSQL 密码，生产环境必须覆盖 |
| `REDIS_HOST` | `localhost` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_PASSWORD` | 空 | Redis 密码 |
| `REDIS_MAX_CONNECTIONS` | `256` | 每个 worker 的 Redis 连接池上限 |
| `REDIS_POOL_TIMEOUT` | `0.25` | Redis 连接池等待超时（秒） |
| `DB_POOL_SIZE` | `8` | 每个 worker 的 PostgreSQL 常驻连接数 |
| `DB_MAX_OVERFLOW` | `4` | 每个 worker 的 PostgreSQL 临时溢出连接数 |
| `DB_POOL_TIMEOUT` | `30` | PostgreSQL 连接池等待超时（秒） |
| `DB_POOL_RECYCLE` | `1800` | PostgreSQL 连接最长复用时间（秒） |
| `SERVER_WORKERS` | 自动 | Uvicorn Worker 数量，自动模式最多使用 4 个 |
| `SERVER_LIMIT_CONCURRENCY` | `512` | 每个 worker 的并发请求上限 |
| `SERVER_TIMEOUT_KEEP_ALIVE` | `5` | HTTP Keep-Alive 超时（秒） |
| `PORT` | `3001` | HTTP 监听端口 |
| `MP_ADMIN_USERS` | 空 | 逗号分隔的管理员 GitHub 用户名 |
| `COMMUNITY_MEDALS_ENABLED` | `false` | 是否向所有 MoviePilot 实例开放社区勋章，经 `GET /user/features` 下发 |
| `COMMUNITY_MEDALS_PREVIEW_USERS` | 空 | 逗号分隔的 GitHub 用户名（不区分大小写），全局关闭时这些用户仍可预览社区勋章；为空表示无人预览 |

生产环境建议使用 PostgreSQL 和 Redis，并通过反向代理启用 TLS。该服务只应接收 MoviePilot 约定的数据，不要通过分享或统计接口上传敏感内容。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

运行测试：

```bash
pytest tests
```

## 管理后台

通过 `/admin/` 访问 Server 管理后台，支持多用户登录、超级管理员账号管理、统计查询、共享与评分管理、识别缓存、功能开关和维护审计。没有默认账号或公开注册入口。

首次部署后执行 `docker exec -it moviepilot-server python -m app.admin.cli admin`，交互输入临时密码。后台需经 HTTPS 访问，Nginx 中转规则已加入 `docker/nginx.conf`；更新实际代理配置后先检查再重载。

完整初始化、权限、配置、前端构建与部署说明见 [管理后台文档](docs/ADMIN.md)。

## 私有 App 消息推送

部署配置、自动接入、设备绑定与通知使用说明见 [私有 App 消息推送文档](docs/PUSH.md)。

## 许可证

许可证内容见 [docs/LICENSE](docs/LICENSE)。
