# 私有 App 消息推送

[返回项目首页](../README.md)

MoviePilot → 你的 MoviePilot-Server → APNs / Web Push → 手机。无需使用其他 App 作者的中转服务。设备绑定持久化到数据库；一次性绑定码、限流和网页通知的短期全文保存在 Redis。

| 安装方式 | 接收方式 | 通知归属 |
| --- | --- | --- |
| 免费 Apple Personal Team 自签 | Safari 添加到主屏幕的通知网页，iOS 16.4+ | 主屏幕“MoviePilot 通知” |
| 有 Push Notifications 能力的有效签名 | App 原生 APNs | MoviePilot App |

Server 无法替免费签名补上 APNs 权限。WKWebView 也不会继承 Safari/PWA 的订阅。两种方式都使用系统推送服务，手机无需让 App 常驻前台；实际到达仍受网络、系统通知权限和专注模式影响。

## 配置 Server

Server 可以由运营者统一部署，为许多用户自己的 MoviePilot 容器提供通知中转。普通用户无需注册 Server 账号，也不需要找运营者索取密钥。

1. 运营者持久保存一份 VAPID 私钥：

   ```sh
   mkdir -p /path/to/moviepilot-server/push
   chmod 700 /path/to/moviepilot-server/push
   openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 \
     -out /path/to/moviepilot-server/push/vapid.pem
   chmod 600 /path/to/moviepilot-server/push/vapid.pem
   ```

2. 在 Server 容器添加以下环境变量，并沿用现有数据库、Redis 和 `/config` 持久卷：

   ```dotenv
   PUSH_AUTO_REGISTER=true
   PUSH_VAPID_KEY_FILE=/config/push/vapid.pem
   PUSH_VAPID_SUBJECT=mailto:your-email@example.com
   ```

   自动注册默认关闭，运营者显式设置 `PUSH_AUTO_REGISTER=true` 后才接受新实例；关闭该开关不影响已登记实例。每个容器安全随机生成并先行保存自己的256位凭据，Server 只保存摘要；不提供密钥列表、按 UID 取回密钥或重置他人凭据的接口。

   Server 沿用客户端上报的 `user_uid` 关联部署来源。UID 不作为密码，也不证明自然人身份；同一 UID 的不同凭据形成独立空间，不会认领或覆盖已有设备。容器内的多用户继续按不可变本地用户 ID 隔离，用户名相同不会串号。数据库和 Redis 分别保存长期绑定与短期配对能力。

   默认每个来源每小时最多登记10个新实例，整个服务每小时最多1000个，可用 `PUSH_REGISTRATION_PER_HOUR` 和 `PUSH_REGISTRATION_GLOBAL_PER_HOUR` 调整。相同凭据重试不重复消耗新注册配额。公开注册仍可能遭到滥用，应结合入口容量和运营策略管理；UID 自报不等于实名认证。

   兼容已有手工接入：用 `openssl rand -hex 32` 生成实例独立密钥，多个值以英文逗号连接到 `PUSH_RELAY_KEYS`。不要把全局共用密钥分发给所有容器。手工密钥变更会形成新空间、需要重新绑定；自动接入的凭据轮换保留原设备。密钥不应进入 URL、Git 仓库或访问日志。

3. 为 Server 提供手机可访问、证书受信任的 **HTTPS** 域名。将完整 `/push/` 路径代理到 Server 的 3001 端口，保留 Authorization 头，关闭该路径的共享缓存。不要改写为 `/api/push/`。例如：

   ```nginx
   location /push/ {
       proxy_pass http://moviepilot-server:3001;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_set_header X-Forwarded-For $remote_addr;
       proxy_read_timeout 70s;
       proxy_cache off;
   }
   ```

   将反向代理实际来源 IP 加入 Uvicorn 的 `FORWARDED_ALLOW_IPS` 环境变量，使用明确地址名单，避免将其设为 `*`；同时限制外部直接访问应用端口。仅可信代理可提供转发地址，Nginx 应覆盖客户端自带的转发头，才能按实际用户来源限流。未配置可信代理时会按代理连接地址共享配额。

   `GET /push/config` 只公开 VAPID 公钥与服务开关，不返回发送密钥。绑定和管理响应不会共享缓存。匿名接口按连接 IP 限流，每个实例默认每分钟60次请求，每个本地用户最多20个设备。Server 出站需能连接浏览器的推送服务。

4. 运营者可在管理后台的 **推送实例** 中按 UID 或实例标识检索、停用和恢复自动接入实例。此入口仅超级管理员可用，写操作受 CSRF、明确确认和审计保护；列表只显示脱敏 UID、状态和设备数量，不显示凭据摘要或设备地址。停用后旧凭据不能重新注册复活，已有网页也不能继续绑定或发送测试通知；用户仍可自行解绑。已交给系统推送服务的在途消息无法撤回。

运营者维护 HTTPS、数据库/Redis、备份、VAPID 私钥、可选 Apple 私钥及服务容量；容器接入、设备注册、令牌更新和失效清理由程序处理。VAPID 私钥必须持久保存，更换后浏览器需重新订阅。Server 可以接触待转发消息正文和设备地址，本服务未实现面向运营者的端到端加密。

## 连接 MoviePilot 与自签 App

1. 更新 MoviePilot 后端与兼容前端，在 **设定 → 通知** 新增 **AppPush / App 私有推送**，启用渠道及所需消息类型。地址留空使用 MoviePilot 已配置的中心服务（`MP_SERVER_HOST`），也可填写运营者提供的 HTTPS 根地址；自动接入无需填写密钥。已有手工密钥放在“手动接入现有服务”中，填写后使用兼容模式。
2. 消息路由选择现有的“管理员”或“用户”。用户身份由后台账户自动绑定，无需填写用户名或第三方 PushKey。选择无用户目标的“所有渠道”不会触发隐式设备广播。
3. App 右上角 **三个点 → 连接与显示 → 通知 → 生成绑定码**。在 Safari 打开 `https://你的Server/push/app/`，添加到主屏幕，再从主屏幕打开，粘贴十分钟内有效的一次性码，点击 **绑定并允许通知**。
4. 从主屏幕网页发送测试通知。点击系统通知后，详情页显示本条通知的标题、正文和海报，并提供 **在 MoviePilot App 中打开** 链接；详情中隐藏测试通知及绑定管理，可从 **通知设置** 进入管理。没有通知记录时显示绑定设置。App 等待登录完成，再校验完整实例地址和不可变账号身份，交给原网页路由/通知中心。旧消息没有海报时正常显示文字，图片加载失败也不影响跳转。

海报展示需要先更新 Server，再更新 MoviePilot 后端；已有配对无需重建，打开通知网页后会检查静态资源更新。系统推送携带图片和图标字段，实际锁屏展示以设备及浏览器支持为准；详情页由接收设备加载无凭据的 HTTP(S) 图片，图片源必须可访问，HTTPS 网页中的 HTTP 图片还受浏览器混合内容策略限制。Server 不代理下载图片，也不向图片源转发 MoviePilot 登录凭据。

网页通知全文支持最多65536个字符，不再截取前1000个字符。Server 在 Redis 中按设备绑定版本暂存最近20条全文和图片地址，每条独立在24小时后过期，不保存海报文件。系统推送携带大小受限的预览和随机标识，网页通过 `POST /push/subscriptions/message` 以本绑定的设备标识和管理令牌读取全文；随机消息标识本身不能读取内容，其他设备、换绑和停用后的能力均不能读取。正文和令牌不放进 URL。

worker 在收到通知及点击时将消息交接记录写入本设备的独立私有缓存，最多20条、有效期24小时；超期记录不再显示，在后续访问时清理。页面启动、恢复和重载都会读取，不再仅依赖地址片段。系统只恢复起始页、未交付点击信息时展示“最近收到的通知”，避免把它错误标作某条已确认的点击。已读取的全文也可在有效期内离线重看；解绑/换绑时清除本地缓存。超期、网络故障或归属不匹配会显示明确提示，不再无声退回绑定页。永久业务历史仍保存在 MoviePilot 后端通知中心。

主屏幕网页只保存本设备的解绑、测试及短期全文读取能力，不保存 MoviePilot 登录令牌。绑定能力同时保存到 localStorage 与独立私有缓存，后者不受通知条数及24小时清理影响，解绑时一起清除。localStorage 缺失或不可读时先尝试本机备份，再使用浏览器现有订阅调用 `POST /push/subscriptions/restore`；必须同时匹配端点、p256dh 和私有 auth，不能仅凭端点或设备标识恢复。恢复能力按绑定版本派生，不使原能力失效，也不创建新订阅；换绑、解绑和实例停用仍阻断旧能力。旧 worker 没有本地消息时通过 `POST /push/subscriptions/latest` 读取本设备最新暂存全文。

设备端点仅接受 Apple、Mozilla、Google FCM 和 Windows 的已知 HTTPS 推送服务。删除主屏幕网页、更换 VAPID 密钥或清除包括系统订阅在内的全部网站数据后需要重新绑定；仅丢失网页绑定记录而仍保留订阅时会自动恢复。重新绑定覆盖当前浏览器的旧订阅。已有绑定打开一次网页即可检查 worker 更新，再用新收到的通知验证；更新前已经投递且未暂存的通知无法补回原正文。

App 的 **解除本机及配对网页绑定** 会撤销该安装关联的 APNs 和网页设备，不影响其他安装。在线退出登录会尝试同样清理；离线退出只能先清理本地状态，请在通知网页解除旧绑定。已经被系统接受的在途通知无法召回。

容器管理员可在已保存的通知渠道中点击 **轮换接入凭据**，原设备无需重新绑定。候选凭据先落库，中心接受后旧凭据立即失效；若响应丢失，容器重启后重试同一次轮换。自动接入状态保存在 MoviePilot 数据库的内部配置中，备份或迁移容器时应保留；丢失凭据不能凭 UID 取回，需要建立新空间并重新绑定设备。不要将同一数据库备份同时用于两个独立运营的容器，否则它们会共享原实例凭据。

## 可选原生 APNs

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
