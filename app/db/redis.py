"""
Redis连接管理
"""
import logging
import time
from typing import Optional

from redis.asyncio import BlockingConnectionPool, Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client: Optional[Redis] = None
CONNECTION_ERROR_LOG_INTERVAL = 60.0
_last_connection_error_logged_at: Optional[float] = None
_suppressed_connection_errors = 0


def log_connection_error(err: Exception) -> None:
    """按进程汇总 Redis 连接错误，避免过载时日志写入放大故障。"""
    global _last_connection_error_logged_at, _suppressed_connection_errors

    now = time.monotonic()
    if (
            _last_connection_error_logged_at is not None
            and now - _last_connection_error_logged_at < CONNECTION_ERROR_LOG_INTERVAL
    ):
        _suppressed_connection_errors += 1
        return

    suppressed = _suppressed_connection_errors
    _last_connection_error_logged_at = now
    _suppressed_connection_errors = 0
    logger.warning("Redis connection unavailable: %s; suppressed=%d", err, suppressed)


async def init_redis() -> Redis:
    """初始化Redis连接"""
    global redis_client
    if redis_client is not None:
        return redis_client

    pool = BlockingConnectionPool.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        timeout=settings.REDIS_POOL_TIMEOUT,
        socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    )
    client = Redis(connection_pool=pool)

    try:
        await client.ping()
    except Exception as err:
        await client.aclose(close_connection_pool=True)
        logger.error(f"Redis connection init failed: {err}")
        raise

    redis_client = client
    logger.info("Redis connection initialized")
    return redis_client


def get_redis() -> Redis:
    """获取已初始化的Redis客户端"""
    if redis_client is None:
        raise RuntimeError("Redis client is not initialized")
    return redis_client


async def close_redis():
    """关闭Redis连接"""
    global redis_client
    if redis_client is None:
        return

    await redis_client.aclose(close_connection_pool=True)
    redis_client = None
