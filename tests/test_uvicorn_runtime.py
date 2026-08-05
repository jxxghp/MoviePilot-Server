"""Uvicorn 生产运行时的可选性能实现选择测试。"""

from uvicorn import Config


async def _asgi_app(scope, receive, send) -> None:
    """提供无需外部服务的最小 ASGI 应用。"""


def test_auto_runtime_selects_native_implementations() -> None:
    """auto 模式应选择 uvloop 事件循环和 httptools HTTP 解析器。"""
    config = Config(_asgi_app, loop="auto", http="auto")
    config.load()

    assert config.http_protocol_class.__module__ == (
        "uvicorn.protocols.http.httptools_impl"
    )

    loop_factory = config.get_loop_factory()
    assert loop_factory is not None
    loop = loop_factory()
    try:
        assert type(loop).__module__.startswith("uvloop")
    finally:
        loop.close()
