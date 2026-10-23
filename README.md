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

## 许可证

许可证内容见 [docs/LICENSE](docs/LICENSE)。

## 管理后台

通过 `/admin/` 访问 Server 管理后台，支持多用户登录、超级管理员账号管理、统计查询、共享与评分管理、识别缓存、功能开关和维护审计。没有默认账号或公开注册入口。

首次部署后执行 `docker exec -it moviepilot-server python -m app.admin.cli admin`，交互输入临时密码。后台需经 HTTPS 访问，Nginx 中转规则已加入 `docker/nginx.conf`；更新实际代理配置后先检查再重载。

完整初始化、权限、配置、前端构建与部署说明见 [管理后台文档](docs/ADMIN.md)。

## 私有 App 消息推送

MoviePilot → 你的 MoviePilot-Server → APNs / Web Push → 手机。无需使用其他 App 作者的中转服务。消息正文只在投递时处理；设备绑定持久化到数据库，一次性绑定码和限流保存在 Redis。

| 安装方式 | 接收方式 | 通知归属 |
| --- | --- | --- |
| 免费 Apple Personal Team 自签 | Safari 添加到主屏幕的通知网页，iOS 16.4+ | 主屏幕“MoviePilot 通知” |
| 有 Push Notifications 能力的有效签名 | App 原生 APNs | MoviePilot App |

Server 无法替免费签名补上 APNs 权限。WKWebView 也不会继承 Safari/PWA 的订阅。两种方式都使用系统推送服务，手机无需让 App 常驻前台；实际到达仍受网络、系统通知权限和专注模式影响。

### 配置 Server

1. 持久保存 VAPID 私钥和实例中转密钥：

   ```sh
   mkdir -p /path/to/moviepilot-server/push
   chmod 700 /path/to/moviepilot-server/push
   openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 \
     -out /path/to/moviepilot-server/push/vapid.pem
   chmod 600 /path/to/moviepilot-server/push/vapid.pem
   openssl rand -hex 32
   ```

2. 在 Server 容器添加以下环境变量，并沿用现有数据库、Redis 和 `/config` 持久卷：

   ```dotenv
   PUSH_RELAY_KEYS=上一步生成的至少32位随机密钥
   PUSH_VAPID_KEY_FILE=/config/push/vapid.pem
   PUSH_VAPID_SUBJECT=mailto:your-email@example.com
   ```

   多个 MoviePilot 实例分别使用不同密钥，以英文逗号连接到 `PUSH_RELAY_KEYS`。不要把密钥写入网页、URL、Git 仓库或反向代理访问日志。VAPID 私钥必须持久保存；更换后浏览器需要重新订阅。中转密钥变更会形成新命名空间，原设备需要重新绑定。

3. 为 Server 提供手机可访问、证书受信任的 **HTTPS** 域名。将完整 `/push/` 路径代理到 Server 的 3001 端口，保留 Authorization 头，关闭该路径的共享缓存。不要改写为 `/api/push/`。例如：

   ```nginx
   location /push/ {
       proxy_pass http://moviepilot-server:3001;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_read_timeout 70s;
       proxy_cache off;
   }
   ```

   `GET /push/config` 只返回 VAPID 公钥。绑定和管理接口不会缓存。生产环境应限制直接访问应用端口，只信任自己的反向代理；匿名接口按连接 IP 限流，每个中转密钥默认每分钟 60 次请求，每个账号最多 20 个设备。Server 出站需能连接浏览器的推送服务。

### 连接 MoviePilot 与自签 App

1. 更新 MoviePilot 后端与兼容前端，在 **设定 → 通知** 新增 **AppPush / App 私有推送**，填写 Server 的 HTTPS 根地址和对应的中转密钥，启用渠道及所需消息类型。
2. 消息路由选择现有的“管理员”或“用户”。用户身份由后台账户自动绑定，无需填写用户名或第三方 PushKey。选择无用户目标的“所有渠道”不会触发隐式设备广播。
3. App 右上角 **三个点 → 连接与显示 → 通知 → 生成绑定码**。在 Safari 打开 `https://你的Server/push/app/`，添加到主屏幕，再从主屏幕打开，粘贴十分钟内有效的一次性码，点击 **绑定并允许通知**。
4. 从主屏幕网页发送测试通知。点击系统通知后，可选择 **在 App 中查看**；App 等待登录完成，再校验完整实例地址和不可变账号身份，交给原网页路由/通知中心。

主屏幕网页只保存本设备的解绑/测试能力，不保存 MoviePilot 登录令牌。设备端点仅接受 Apple、Mozilla、Google FCM 和 Windows 的已知 HTTPS 推送服务。删除主屏幕网页、更换 VAPID 密钥或清除网站数据后需要重新绑定；重新绑定覆盖当前浏览器的旧订阅。

App 的 **解除本机及配对网页绑定** 会撤销该安装关联的 APNs 和网页设备，不影响其他安装。在线退出登录会尝试同样清理；离线退出只能先清理本地状态，请在通知网页解除旧绑定。已经被系统接受的在途通知无法召回。

### 可选原生 APNs

仅在具备 Apple 团队、带 Push Notifications 能力的描述文件和 APNs Auth Key 时配置：

```dotenv
PUSH_APNS_KEY_FILE=/config/push/AuthKey.p8
PUSH_APNS_TEAM_ID=你的TeamID
PUSH_APNS_KEY_ID=你的KeyID
PUSH_APNS_TOPICS=org.example.moviepilot
```

`.p8` 私钥只挂载在 Server，不能放入 App。Bundle ID 必须出现在 topics 允许名单中；多个值以英文逗号连接。App 的开发/生产签名分别注册 sandbox/production 令牌，Server 使用对应 Apple HTTP/2 地址和 ES256 provider token。App 的签名步骤见 [MoviePilot-App README](https://github.com/jxxghp/MoviePilot-App#消息通知)。APNs 410 / Web Push 404、410 会清理失效绑定；清理带版本判断，避免旧失败删除刚轮换的令牌。

此功能默认关闭，没有配置密钥时管理和发送接口不可用。测试覆盖实例/用户隔离、一次性码防重放、注销、令牌轮换、真实载荷加密和签名；本机传输替身或模拟器注入不代表已通过真实 iPhone 的后台、锁屏投递验收。

实现参考了 [MoviePilotLite](https://github.com/singleton-altman/MoviePilotLite) 的设备绑定与通知点击流程，以及 [MoviePilotAppPush](https://github.com/buzhengg/MoviePilotAppPush) 的 APNs 中转思路，独立实现本服务的鉴权与双通道投递。Lite 的 JPush 在 iOS 最终也依赖 APNs；免费自签采用 [WebKit 支持的主屏幕 Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)。通知网页的品牌图标沿用 MoviePilot-Frontend / MoviePilot-App 的 MIT 资源。
