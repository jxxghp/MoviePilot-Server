"""提供编译后的后台页面，未知管理 API 永远不回退到 SPA。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DIST = Path(__file__).resolve().parents[2] / "admin-ui" / "dist"


class AdminHeadersMiddleware:
    """仅拦截后台响应头，公共请求直接转发且不额外创建任务或响应流。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游 ASGI 应用，所有请求状态均留在调用局部变量中。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """后台响应保留安全头，其他 HTTP 路径和非 HTTP 连接直接通过。"""
        path = scope.get("path", "")
        if scope["type"] != "http" or (path != "/admin" and not path.startswith("/admin/")):
            await self.app(scope, receive, send)
            return

        async def send_headers(message: Message) -> None:
            """只修改响应起始消息，保留流式正文和原有状态码。"""
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update({
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                })
            await send(message)

        await self.app(scope, receive, send_headers)


def install_admin_pages(app: FastAPI) -> None:
    """静态文件仅从固定编译目录提供，不允许通过页面路径读取项目源码。"""
    app.mount("/admin/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="admin-assets")

    app.add_middleware(AdminHeadersMiddleware)

    @app.get("/admin", include_in_schema=False)
    async def admin_redirect():
        """统一后台入口尾斜线，保证静态资源和反向代理路径一致。"""
        return RedirectResponse("/admin/", status_code=307)

    @app.get("/admin/{path:path}", include_in_schema=False)
    async def admin_page(path: str):
        """后台前端路由回退，仅公开页面壳，数据由认证 API 返回。"""
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "管理接口不存在")
        if path == "logo.png":
            file = DIST / "logo.png"
        elif path in {"", "login", "overview", "usage", "subscriptions", "shares", "plugins", "recognition", "media-mappings", "features", "system", "users", "push", "audit"}:
            file = DIST / "index.html"
        else:
            raise HTTPException(404, "页面不存在")
        if not file.is_file():
            raise HTTPException(503, "后台页面尚未构建，请在 admin-ui 执行 npm ci && npm run build")
        return FileResponse(file)
