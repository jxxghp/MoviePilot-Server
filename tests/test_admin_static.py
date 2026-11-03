"""验证后台响应安全头、公共接口直通与流式 ASGI 消息保持不变。"""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.admin import static


def test_admin_headers_cover_pages_assets_redirects_and_errors(tmp_path, monkeypatch):
    """后台页面、静态资源、重定向及拒绝响应都保留完整安全头。"""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>admin</html>")
    (tmp_path / "assets" / "app.js").write_text("window.admin = true")
    monkeypatch.setattr(static, "DIST", tmp_path)

    async def run():
        """请求真实 FastAPI 路由并核对状态码和缓存边界。"""
        app = FastAPI()

        @app.get("/admin/api/denied")
        async def denied():
            """模拟后台认证拒绝，检查错误响应的安全头。"""
            raise HTTPException(401, "authentication required")

        static.install_admin_pages(app)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path, status in [("/admin", 307), ("/admin/", 200), ("/admin/assets/app.js", 200),
                                 ("/admin/api/denied", 401), ("/admin/api/missing", 404), ("/admin/missing", 404)]:
                response = await client.get(path)
                assert response.status_code == status
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-content-type-options"] == "nosniff"
                assert response.headers["x-frame-options"] == "DENY"
                assert response.headers["referrer-policy"] == "same-origin"
                assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    asyncio.run(run())


def test_public_routes_keep_their_own_headers():
    """推送与公共接口不继承后台的缓存和 CSP 策略。"""
    async def run():
        """同时覆盖相似路径前缀，避免误将普通接口识别为后台。"""
        app = FastAPI()

        async def public_response():
            """返回公共路由独立定义的响应头。"""
            return JSONResponse({"ok": True}, headers={"Cache-Control": "public, max-age=60"})

        for path in ["/", "/user/features", "/push/config", "/administrator"]:
            app.add_api_route(path, public_response)
        static.install_admin_pages(app)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in ["/", "/user/features", "/push/config", "/administrator"]:
                response = await client.get(path)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "public, max-age=60"
                assert "content-security-policy" not in response.headers
                assert "x-frame-options" not in response.headers
    asyncio.run(run())


@pytest.mark.parametrize("scope", [{"type": "http", "path": "/recognize/share"},
                                   {"type": "websocket", "path": "/admin/socket"}, {"type": "lifespan"}])
def test_non_admin_traffic_preserves_task_and_asgi_channels(scope):
    """普通流量保持原有任务及连接通道，不引入公共请求的任务或流包装。"""
    async def run():
        """让下游直接校验任务身份及原始 ASGI 通道。"""
        task = asyncio.current_task()

        async def receive():
            """提供原始输入通道，由下游检查其身份。"""
            return {"type": "http.disconnect"}

        async def send(message):
            """提供原始输出通道，由下游检查其身份。"""

        async def downstream(current_scope, current_receive, current_send):
            """确认不需要后台响应头的连接完全直通。"""
            assert current_scope is scope
            assert current_receive is receive
            assert current_send is send
            assert asyncio.current_task() is task

        await static.AdminHeadersMiddleware(downstream)(scope, receive, send)
    asyncio.run(run())


def test_admin_streaming_body_is_forwarded_immediately():
    """添加后台响应头不能缓冲正文、吞掉分块或修改返回状态。"""
    async def run():
        """发送两段正文，并在应用继续之前确认第一段已经交付。"""
        messages = []

        async def receive():
            """提供 ASGI 输入占位，本场景不读取请求正文。"""
            return {"type": "http.disconnect"}

        async def send(message):
            """记录实际已经交付给服务器的消息。"""
            messages.append(message)

        async def downstream(scope, receive, send):
            """在生成下一块之前检查上一块已被下游收到。"""
            await send({"type": "http.response.start", "status": 206, "headers": [(b"cache-control", b"public")]})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            assert messages[-1]["body"] == b"first"
            await send({"type": "http.response.body", "body": b"second", "more_body": False})

        await static.AdminHeadersMiddleware(downstream)({"type": "http", "path": "/admin/stream"}, receive, send)
        assert messages[0]["status"] == 206
        assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
        assert messages[1:] == [{"type": "http.response.body", "body": b"first", "more_body": True},
                                {"type": "http.response.body", "body": b"second", "more_body": False}]
    asyncio.run(run())
