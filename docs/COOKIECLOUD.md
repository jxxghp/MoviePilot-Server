# 公共 CookieCloud 限流与异常访问告警

公共 CookieCloud 由 Nginx 代理至独立的 `easychen/cookiecloud` 容器。以下防护在
`docker/nginx.conf` 生效，不改变 CookieCloud 加密格式、KEY、上传或拉取协议。

## 限流

- 每个真实来源 IP 每分钟 5 次请求，按至少 12 秒间隔放行，不允许突发。
- 同一 KEY 的拉取在所有 IP 之间共享每分钟 1 次配额，至少间隔 60 秒，不允许突发。
  GET、HEAD、POST 等方法共用此配额，OPTIONS 预检不占用配额。
- 超限返回 HTTP 429、`Retry-After: 60`，保留跨域响应头。所有响应禁止缓存。
- 使用与 KEY 限流一致的规范化路径转发到上游，防止编码分隔符、点段与上游
  `basename()` 形成路径别名，绕过同一 KEY 的配额。查询参数仍原样转发。
- 仅接受 `docker/cloudflare-realip.conf` 中 Cloudflare 网段提供的
  `CF-Connecting-IP`；直连时使用实际连接地址，忽略伪造的 CF/XFF/X-Real-IP 头。
- `docker/docker-compose.yml` 将 CookieCloud 的 8088 端口绑定至 `127.0.0.1`，
  防止客户端绕过代理。Nginx 使用主机网络时可直接访问；采用独立容器网络时，
  应改为同一私有网络中的服务名，并取消 CookieCloud 的端口发布。

修改配额时调整两个 `limit_req_zone`，保持无 `burst` 以避免短时间连续拉取。多个客户端使用同一
出口或同一 KEY 时共享配额，手动反复同步可能触发 429，等待一分钟后重试。

## 异常访问监控

Nginx 单独写入 `/var/log/nginx/cookiecloud-security.log`，每行只包含时间、真实来源 IP、
操作分类、HTTP 状态和限流结果。KEY 位于请求路径中，因此此 location 禁用普通错误
日志，使用上述脱敏状态记录监控故障；不记录请求路径、参数、请求头、正文或 Cookie。

监控是可选功能，Compose 默认不启动；仅显式启用 `monitoring` profile 时部署。
独立的 `cookiecloud-monitor` 容器读取此日志，不访问 CookieCloud 数据目录、SQL 或 Redis。
最近五个分钟桶内达到以下阈值时输出 `cookiecloud_security_alert` WARNING：

| 异常 | 每个 IP | 全局汇总 |
| --- | --- | --- |
| 拉取不存在的 KEY（404） | 20 次 | 100 次 |
| 请求超限（429，含上游返回） | 20 次 | 100 次 |
| 服务异常（5xx） | — | 10 次 |

全局汇总用于识别轮换 IP 的扫描。同一来源、同一异常类型冷却 15 分钟，避免重复轰炸。
来源跟踪最多 10000 项，淘汰不影响全局计数。监控重启会重新检查日志中的近期记录，
冷却时间从重启后重新计算。支持 rename 和 copytruncate 轮转；推荐 rename 后通知
Nginx 重新打开日志，以减少 copytruncate 本身存在的写入丢失窗口。

默认告警输出到容器日志，可对接现有日志告警系统。可选设置私有环境变量
`COOKIECLOUD_ALERT_WEBHOOK`，向指定 HTTP(S) 接口发送 JSON：

```json
{"alerts":[{"event":"cookiecloud_security_alert","scope":"198.51.100.1","reason":"missing_keys","count":20,"window_seconds":300,"time":1795262400}],"dropped":0}
```

`scope` 为来源 IP 或 `global`；`reason` 为 `missing_keys`、`rate_limited` 或
`upstream_errors`。接口需返回 2xx。每分钟最多投递一批，超时 5 秒，不跟随重定向；
失败保留队列并重试，队列最多 100 项，超出数量计入 `dropped`，所有告警仍写入容器日志。
Webhook URL 与失败响应正文不会输出。请把 URL 放在服务器私有 `.env` 或等效部署配置中。

## 部署与验证

1. 备份线上 Nginx、Compose 和镜像引用。更新镜像不会自动更新实际 Nginx 配置。
2. 同步 `docker/nginx.conf`、`docker/cloudflare-realip.conf`，确保日志目录可写。
   如果 Nginx 运行在容器中，将其 `/var/log/nginx` 挂载到主机专用目录，设置
   `COOKIECLOUD_LOG_DIR` 指向该主机目录，供监控容器只读挂载。
3. 执行实际 Nginx 实例的 `nginx -t`，通过后执行 `nginx -s reload`。
4. 合并线上 Compose 中 CookieCloud 的本机端口绑定。默认仅更新服务端和 CookieCloud，
   不部署监控程序。
   在配置目录中执行：

   ```bash
   docker compose pull moviepilot-server
   docker compose up -d --no-deps moviepilot-server cookiecloud
   ```

   确认 8088 仅监听本机；使用专用测试 KEY 验证上传和拉取，避免在命令行、终端记录中
   使用真实 KEY/密码。需要告警时，再单独执行
   `docker compose --profile monitoring up -d --no-deps cookiecloud-monitor`；用
   `docker compose exec cookiecloud-monitor python3 -m app.services.cookiecloud --once` 检查日志挂载，
   缺少日志或无权限时返回非零。
5. 为脱敏日志配置 logrotate：建议 `daily`、`maxsize 10M`、`rotate 7`、`compress`、
   `delaycompress`、`missingok`、`notifempty`，轮转后对实际 Nginx 执行 `nginx -s reopen`。
   日志属主/权限应允许 Nginx 写入、监控读取。不要把普通包含请求 URI 的日志交给监控。
6. 在隔离环境制造连续 404/429，确认 WARNING；启用 Webhook 时同时确认接收端收到
   `alerts`。测试命令为 `python -m pytest tests/test_cookiecloud_nginx.py tests/test_cookiecloud_monitor.py`。

本防护不能撤回已被拉走的密文，也不能阻止低于阈值的分布式访问。用户仍应使用强随机
KEY/密码；本次配置不变更用户凭据或历史 CookieCloud 数据。
