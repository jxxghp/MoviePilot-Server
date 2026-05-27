"""
请求用户统计服务
"""
import hashlib
import hmac
import logging
from typing import Optional

from cacheout import Cache
from fastapi import Request

from app.core.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)


class RequestUserStatisticService:
    """请求用户统计服务类"""

    UNKNOWN_USERS_KEY = f"{settings.REDIS_KEY_PREFIX}:usage:request_users:unknown"
    REPORTED_USERS_KEY = f"{settings.REDIS_KEY_PREFIX}:usage:request_users:reported"
    REQUEST_FINGERPRINT_STATE_KEY = "request_user_fingerprint"
    REPORT_USER_UID_HEADER = "X-MoviePilot-User-Uid"
    RECENT_FINGERPRINT_CACHE_TTL = 3600
    RECENT_FINGERPRINT_CACHE = Cache(maxsize=65536, ttl=RECENT_FINGERPRINT_CACHE_TTL)
    RECORD_UNKNOWN_USER_SCRIPT = """
    if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
        return 0
    end
    return redis.call('SADD', KEYS[1], ARGV[1])
    """

    @staticmethod
    def should_skip_request(request: Request) -> bool:
        """
        判断当前请求是否需要跳过用户统计。
        """
        if request.method in {"OPTIONS", "HEAD"}:
            return True

        return request.url.path in {
            "/",
            "/usage/statistic",
            "/favicon.ico",
        }

    @staticmethod
    def _is_recent_fingerprint(fingerprint: str) -> bool:
        """
        判断用户指纹是否已在本进程短时间内登记过。
        """
        return RequestUserStatisticService.RECENT_FINGERPRINT_CACHE.get(fingerprint) is not None

    @staticmethod
    def _remember_fingerprints(fingerprints: set[str]) -> None:
        """
        记录本进程短时间内已经处理过的用户指纹。
        """
        for fingerprint in fingerprints:
            RequestUserStatisticService.RECENT_FINGERPRINT_CACHE.set(fingerprint, True)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """
        获取 Cloudflare 转发后的真实客户端 IP。
        """
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        return request.client.host if request.client else ""

    @staticmethod
    def _build_hashed_fingerprint(raw_value: str) -> str:
        """
        将用户识别信息转换为不可逆指纹。
        """
        salt = settings.REDIS_KEY_PREFIX.encode("utf-8")
        return hmac.new(salt, raw_value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _build_user_uid_fingerprint(user_uid: Optional[str]) -> Optional[str]:
        """
        根据安装用户唯一 ID 生成用户指纹。
        """
        if not user_uid:
            return None

        return RequestUserStatisticService._build_hashed_fingerprint(f"uid\0{user_uid}")

    @staticmethod
    def _build_source_fingerprint(request: Request) -> Optional[str]:
        """
        根据客户端来源生成兼容旧客户端的用户指纹。
        """
        client_ip = RequestUserStatisticService._get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")
        if not client_ip and not user_agent:
            return None

        return RequestUserStatisticService._build_hashed_fingerprint(
            f"request\0{client_ip}\0{user_agent}"
        )

    @staticmethod
    def _build_request_fingerprint(request: Request) -> Optional[str]:
        """
        根据请求头或客户端来源生成用户指纹。
        """
        user_uid = request.headers.get(RequestUserStatisticService.REPORT_USER_UID_HEADER)
        if user_uid:
            return RequestUserStatisticService._build_user_uid_fingerprint(user_uid.strip())

        return RequestUserStatisticService._build_source_fingerprint(request)

    @staticmethod
    async def record_request_user(request: Request) -> None:
        """
        将未上报安装版本的请求用户登记到 Redis。
        """
        if RequestUserStatisticService.should_skip_request(request):
            return

        fingerprint = RequestUserStatisticService._build_request_fingerprint(request)
        if not fingerprint:
            return

        setattr(
            request.state,
            RequestUserStatisticService.REQUEST_FINGERPRINT_STATE_KEY,
            fingerprint,
        )
        if RequestUserStatisticService._is_recent_fingerprint(fingerprint):
            return

        redis = get_redis()
        await redis.eval(
            RequestUserStatisticService.RECORD_UNKNOWN_USER_SCRIPT,
            2,
            RequestUserStatisticService.UNKNOWN_USERS_KEY,
            RequestUserStatisticService.REPORTED_USERS_KEY,
            fingerprint,
        )
        RequestUserStatisticService._remember_fingerprints({fingerprint})

    @staticmethod
    async def safe_record_request_user(request: Request) -> None:
        """
        安全登记请求用户，避免统计异常影响正常响应。
        """
        try:
            await RequestUserStatisticService.record_request_user(request)
        except Exception as err:
            logger.warning(f"Record request user skipped: {err}")

    @staticmethod
    async def mark_request_user_reported(
            request: Optional[Request],
            user_uid: Optional[str] = None,
    ) -> None:
        """
        将当前请求用户从“未知”分类移动到已上报安装版本分类。
        """
        fingerprints = set()
        user_uid_fingerprint = RequestUserStatisticService._build_user_uid_fingerprint(user_uid)
        if user_uid_fingerprint:
            fingerprints.add(user_uid_fingerprint)

        if request is not None:
            request_fingerprint = getattr(
                request.state,
                RequestUserStatisticService.REQUEST_FINGERPRINT_STATE_KEY,
                None,
            ) or RequestUserStatisticService._build_request_fingerprint(request)
            if request_fingerprint:
                fingerprints.add(request_fingerprint)
            source_fingerprint = RequestUserStatisticService._build_source_fingerprint(request)
            if source_fingerprint:
                fingerprints.add(source_fingerprint)

        if not fingerprints:
            return

        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.sadd(RequestUserStatisticService.REPORTED_USERS_KEY, *fingerprints)
            pipe.srem(RequestUserStatisticService.UNKNOWN_USERS_KEY, *fingerprints)
            await pipe.execute()
        RequestUserStatisticService._remember_fingerprints(fingerprints)

    @staticmethod
    async def count_other_users() -> int:
        """
        统计尚未上报安装版本的请求用户数量。
        """
        redis = get_redis()
        return await redis.scard(RequestUserStatisticService.UNKNOWN_USERS_KEY)
