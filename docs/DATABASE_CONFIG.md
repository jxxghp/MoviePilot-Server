# MoviePilot Server 数据库配置说明

## 数据库支持

本项目现在支持两种数据库类型：
- **SQLite** (默认)
- **PostgreSQL**

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