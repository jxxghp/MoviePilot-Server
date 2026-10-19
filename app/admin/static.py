"""提供编译后的后台页面，未知管理 API 永远不回退到 SPA。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[2] / "admin-ui" / "dist"


def install_admin_pages(app: FastAPI) -> None:
    """静态文件仅从固定编译目录提供，不允许通过页面路径读取项目源码。"""
    app.mount("/admin/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="admin-assets")

    @app.middleware("http")
    async def admin_headers(request: Request, call_next):
        """后台响应禁止共享缓存并限制脚本、嵌入与跨域资源来源。"""
        response = await call_next(request)
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            response.headers.update({
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            })
        return response

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
        elif path in {"", "login", "overview", "usage", "subscriptions", "shares", "plugins", "recognition", "features", "system", "users", "audit"}:
            file = DIST / "index.html"
        else:
            raise HTTPException(404, "页面不存在")
        if not file.is_file():
            raise HTTPException(503, "后台页面尚未构建，请在 admin-ui 执行 npm ci && npm run build")
        return FileResponse(file)
