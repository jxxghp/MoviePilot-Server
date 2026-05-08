"""
Redis连接管理
"""
import logging
from typing import Optional

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client: Optional[Redis] = None


async def init_redis() -> Redis:
    """初始化Redis连接"""
    global redis_client
    if redis_client is not None:
        return redis_client

    client = Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    )

    try:
        await client.ping()
    except Exception as err:
        await client.aclose()
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

    await redis_client.aclose()
    redis_client = None
