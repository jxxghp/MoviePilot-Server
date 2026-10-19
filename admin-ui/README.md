# Server 管理后台前端

Vue 3、TypeScript、Vite、Pinia、Vue Router、Tailwind CSS 4 和 shadcn-vue。
使用 shadcn-vue 2.8.2 CLI 默认的 `reka-nova` / neutral 配置，组件源码位于 `src/components/ui`，不另行定制配色。
默认 Geist 字体随构建打包，不依赖外部字体服务。默认 CSS variants 从 CLI 原样保留在 `src/components/ui/shadcn.css`，避免将组件生成工具作为构建依赖。

```sh
npm ci
npm run dev
npm test
npm run build
```

开发地址为 `/admin/`，`/admin/api/` 默认代理到 `http://127.0.0.1:3001`。
开发 HTTP 时，后端需显式设置 `ADMIN_COOKIE_SECURE=false`；生产 HTTPS 保持默认 true。
生成物为 `dist/`，由 FastAPI 同源提供，生产环境不需要 Node 运行时。

新增原生组件可使用 `npx shadcn-vue@2.8.2 add <组件>`；生成后检查组件、依赖与默认样式差异。
完整账号初始化、Nginx 和部署说明见仓库 `docs/ADMIN.md`。
