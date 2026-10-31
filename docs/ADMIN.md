# Server 管理后台

后台位于 `/admin/`，管理 API 位于 `/admin/api/`。前端采用 Vue 3、TypeScript 和 shadcn-vue 默认 neutral 样式，保留总览、侧栏、版本分布和最近共享的设计结构。页面不包含演示数据，直接读取当前 Server 的数据库和 Redis。

## 初始化账号

没有默认账号、默认密码或公开注册入口。先启动服务，或在服务相同配置的环境中执行一次初始化命令：

```sh
# Docker，交互输入临时密码，不要把密码写在命令行中
 docker exec -it moviepilot-server python -m app.admin.cli admin

# 本地开发：使用本仓虚拟环境
 .venv/bin/python -m app.admin.cli admin
```

密码长度为 12–128 位。首次登录必须修改临时密码，修改完成后重新登录。
初始化只允许执行一次；后续由超级管理员在“用户管理”中新增账号。
用户名不区分大小写，使用 3–64 位字母、数字、点、下划线或短横线。

忘记所有超级管理员密码时，拥有服务器终端访问权的维护者可以恢复已有的超级管理员：

```sh
 docker exec -it moviepilot-server python -m app.admin.cli admin --recover
```

恢复会启用账号、撤销全部旧会话并要求首次改密。恢复操作写入审计记录，不创建普通账号、不提升既有账号的角色。

## 权限

| 能力 | 管理员 | 超级管理员 |
| --- | --- | --- |
| 总览、版本与安装实例、订阅统计 | 查看、定向删除业务明细 | 同左 |
| 订阅/工作流分享、插件安装统计与评分 | 查看、定向删除 | 同左 |
| 识别缓存 | 分批查看、定向删除 | 同左 |
| 功能开关及 GitHub 权限名单 | 只读 | 修改 |
| 服务状态、操作记录 | 查看 | 查看 |
| 数据清理 | 只读规则和执行结果 | 预览、确认执行 |
| 后台账号 | 修改本人密码 | 创建账号、编辑角色、启停账号、重置密码 |

账号采用停用方式保留历史身份，不提供物理删除账号或删除审计的 API。
至少保留一个启用的超级管理员，用户变更通过数据库写锁串行保护。
后台账号与 `MP_ADMIN_USERS`（MoviePilot 实例的 GitHub 权限名单）相互独立。

密码使用 Argon2 哈希；会话使用随机标识、Redis 过期时间和 HttpOnly Cookie，前端不在 localStorage 保存凭证。
账号被禁用、角色变化、密码修改/重置后，数据库会话版本立即失效。
写操作需要 CSRF 标识。共享内容始终按文本显示，历史工作流执行上下文不下发。
登录同时按账号及来源 IP 限流，默认每 15 分钟最多 10 次尝试；Redis 不可用时认证失败关闭。

## 配置与功能

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ADMIN_COOKIE_SECURE` | `true` | 仅 HTTPS 发送登录 Cookie；本地 HTTP 调试显式设为 false |
| `ADMIN_SESSION_HOURS` | `8` | 会话固定有效期，限制为 1–168 小时 |
| `ADMIN_LOGIN_ATTEMPTS` | `10` | 每个来源 IP/账号在窗口内允许的登录尝试次数 |
| `ADMIN_LOGIN_WINDOW_SECONDS` | `900` | 登录限流窗口（秒） |

“权限与功能”中保存的配置存入 `ADMIN_SETTING` 表，所有 worker 读取相同配置。
未在线保存时沿用 `COMMUNITY_MEDALS_ENABLED`、`COMMUNITY_MEDALS_PREVIEW_USERS`、`MP_ADMIN_USERS` 环境变量。
在线保存后以数据库为准，后续单改环境变量不会覆盖已经保存的设置。
勋章全局开关经 `/user/features` 下发，Nginx 的公开缓存通常在 60 秒内刷新。
预览名单为空时无人预览，包括管理员；全局开放则对所有实例生效。

安装与活跃按现有实例最近上报时间统计，聚合报表最长缓存 30 分钟；当前没有历史日快照，不展示历史折线趋势。
插件安装统计中的 count 是累计安装次数，不是去重安装实例数；评分人数基于实例 UID。
删除评分会在同一事务内重算平均分和人数。后台业务删除会通过 Redis 修订号使其他 worker 缓存通常在 3 秒内失效。
识别缓存使用有界 SCAN，不使用 KEYS；分批扫描结果可能为空但仍有后续游标，点击“继续扫描”。
删除识别记录后，已有公开识别查询的 Nginx 缓存最多可能仍保留 10 分钟。

清理规则与既有服务一致：低频且长期未更新的插件统计、超过 365 天的订阅分享、低频或早期年份的订阅统计。
保留既有调度计算方式：每次调度设置 7 天后的零点（以服务器时区为准）。后台展示实际计算的下次时间；未新增每次查询动态调度。
清理支持预览、文字确认、分布式锁和审计记录。预览到执行期间数据可能变化。
115 仅显示配置是否完整，不显示应用密钥、授权会话或用户访问令牌。

## Nginx 中转

`docker/nginx.conf` 已在 `movie-pilot.org` 的 server 中增加：

- `location = /admin`：转到 `/admin/`。
- `location ^~ /admin/`：将页面、静态资源与管理 API 一并转发到 `moviepilot_server`。
- `proxy_pass http://moviepilot_server;` **不加尾斜线**，保留 `/admin/` 前缀。
- 转发 Host、真实 IP、X-Forwarded-For、X-Forwarded-Proto。
- 显式关闭代理缓存并返回 `Cache-Control: no-store`。
- `^~` 避免 `.js/.css/.png` 被官网通用静态缓存规则接管。

发布代码不会自动安装或重载生产 Nginx；将仓库配置应用到实际反向代理后，先 `nginx -t` 再 reload。
生产入口必须是 HTTPS，并保持 `ADMIN_COOKIE_SECURE=true`。
Uvicorn 仅应信任实际反向代理地址；本机 Nginx 默认环回来源符合 Uvicorn 默认代理信任设置。
如代理通过容器网段访问，显式配置受信任代理地址，不使用任意来源的转发头作为可信客户端身份。

Docker Compose 中 Redis 使用服务名 `redis:6379`，与宿主机映射的 6380 端口分开。
前端资源随 Server 镜像发布，运行时不需要额外 Node 服务或独立前端容器。
镜像从当前构建上下文复制源码，避免构建过程中再次 clone 分支而混入不同提交。

## 本地开发与验证

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
cd admin-ui
npm ci
npm test
npm run build
```

构建后由 `main:App` 提供 `/admin/`；开发服务器支持热更新并代理 `/admin/api` 至本地 3001。
Docker 使用多阶段构建；GitHub workflow 先运行后端测试、前端测试和构建，再构建多架构镜像。

版本分布增加 iOS App 版本。`POST /usage/ios/report` 只接收随机安装 UUID 与版本号；按安装保存最新版本，升级覆盖旧版本，多实例连接不重复计数，不计入后端实例总数及活跃数。新表由现有启动结构初始化自动创建，无需修改已有统计表。App 在所连接实例允许分享使用统计时上报，每个版本最多每天成功上报一次，不上传实例地址、账号或令牌。
