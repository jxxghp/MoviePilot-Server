"""Redis 连接过载的 API 降级与日志抑制测试。"""

import asyncio
import logging
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

import app.db.redis as redis_module
from app.core.config import Settings
from app.services.media_recognize_share import media_recognize_share_service
from main import App, redis_connection_error_handler


def _reset_error_log_state() -> None:
    """清理进程级错误汇总状态，避免测试间互相影响。"""
    redis_module._last_connection_error_logged_at = None
    redis_module._suppressed_connection_errors = 0


def test_overload_defaults_match_bounded_production_limits() -> None:
    """连接池与并发默认值应保持有界，避免无配置实例放大过载。"""
    assert Settings.model_fields["REDIS_MAX_CONNECTIONS"].default == 256
    assert Settings.model_fields["REDIS_POOL_TIMEOUT"].default == 0.25
    assert Settings.model_fields["SERVER_LIMIT_CONCURRENCY"].default == 512


def test_redis_connection_error_returns_retryable_503(monkeypatch) -> None:
    """Redis 连接不可用时返回可退避且不可缓存的受控响应。"""

    async def run_scenario() -> None:
        _reset_error_log_state()
        query = AsyncMock(
            side_effect=RedisConnectionError("No connection available.")
        )
        monkeypatch.setattr(media_recognize_share_service, "query", query)
        transport = ASGITransport(app=App)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/recognize/share",
                params={"keyword": "test", "type": "movie"},
            )

        assert response.status_code == 503
        assert response.json() == {"detail": "Service temporarily unavailable"}
        assert response.headers["Retry-After"] == "1"
        assert response.headers["Cache-Control"] == "no-store"
        query.assert_awaited_once()

    assert App.exception_handlers[RedisConnectionError] is redis_connection_error_handler
    asyncio.run(run_scenario())


def test_redis_connection_error_logging_is_rate_limited(monkeypatch, caplog) -> None:
    """同一 worker 的连续 Redis 连接错误每分钟只输出一次汇总日志。"""
    _reset_error_log_state()
    timestamps = iter((100.0, 101.0, 161.0))
    monkeypatch.setattr(redis_module.time, "monotonic", lambda: next(timestamps))

    with caplog.at_level(logging.WARNING, logger=redis_module.logger.name):
        redis_module.log_connection_error(
            RedisConnectionError("No connection available.")
        )
        redis_module.log_connection_error(
            RedisConnectionError("No connection available.")
        )
        redis_module.log_connection_error(
            RedisConnectionError("No connection available.")
        )

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert "suppressed=1" in messages[-1]
