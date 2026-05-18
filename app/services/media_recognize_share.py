"""
共享媒体识别服务
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from app.core.config import settings
from app.db.redis import get_redis
from app.schemas.models import MediaRecognizeShareItem

logger = logging.getLogger(__name__)


class MediaRecognizeShareService:
    """共享媒体识别服务类"""

    def __init__(self):
        self._started = False

    @property
    def _cache_namespace(self) -> str:
        """获取Redis缓存命名空间"""
        return settings.media_recognize_share_redis_prefix

    def _item_key(self, cache_key: str) -> str:
        """共享识别项存储键"""
        return f"{self._cache_namespace}:item:{cache_key}"

    @classmethod
    def _build_item_cache_key(
            cls,
            keyword: Optional[str],
            media_type: Optional[str],
            year: Optional[str] = None,
            season: Optional[int] = None,
    ) -> Optional[str]:
        """
        构造共享识别项的Redis缓存键。
        """
        cache_key = cls._build_cache_key(
            keyword=keyword,
            media_type=media_type,
            year=year,
            season=season,
        )
        return cache_key

    @staticmethod
    def _normalize_keyword(keyword: Optional[str]) -> str:
        """
        规范化关键字，避免大小写和连续空白导致同一条记录出现多份
        """
        if not keyword:
            return ""
        return re.sub(r"\s+", " ", str(keyword)).strip().lower()

    @staticmethod
    def _normalize_year(year: Optional[str]) -> Optional[str]:
        """
        规范化年份
        """
        if year is None:
            return None
        year_text = str(year).strip()
        return year_text or None

    @staticmethod
    def _normalize_media_type(media_type: Optional[str]) -> Optional[str]:
        """
        统一媒体类型，兼容中文和 agent 风格的传参
        """
        if not media_type:
            return None
        normalized = str(media_type).strip().lower()
        if normalized in {"movie", "电影"}:
            return "movie"
        if normalized in {"tv", "电视剧"}:
            return "tv"
        return None

    @classmethod
    def _normalize_season(
            cls, media_type: Optional[str], season: Optional[int]
    ) -> Optional[int]:
        """
        电影不记录季信息，电视剧季号统一为正整数
        """
        if cls._normalize_media_type(media_type) != "tv":
            return None
        if season in (None, "", 0, "0"):
            return None
        try:
            season_value = int(season)
        except (TypeError, ValueError):
            return None
        return season_value if season_value > 0 else None

    @classmethod
    def _build_cache_key(
            cls,
            keyword: Optional[str],
            media_type: Optional[str],
            year: Optional[str] = None,
            season: Optional[int] = None,
    ) -> Optional[str]:
        """
        构造缓存主键
        """
        keyword_key = cls._normalize_keyword(keyword)
        type_key = cls._normalize_media_type(media_type)
        if not keyword_key or not type_key:
            return None
        year_key = cls._normalize_year(year) or ""
        season_key = str(cls._normalize_season(type_key, season) or "")
        return f"{keyword_key}|{type_key}|{year_key}|{season_key}"

    @classmethod
    def _normalize_item_dict(cls, item: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        规范化共享识别项
        """
        keyword = str(item.get("keyword") or "").strip()
        media_type = cls._normalize_media_type(item.get("type"))
        cache_key = cls._build_cache_key(
            keyword=keyword,
            media_type=media_type,
            year=item.get("year"),
            season=item.get("season"),
        )
        if not cache_key:
            return None

        season = cls._normalize_season(media_type, item.get("season"))
        doubanid = item.get("doubanid")
        title = item.get("title")

        return {
            "keyword": keyword,
            "type": media_type,
            "year": cls._normalize_year(item.get("year")),
            "season": season,
            "tmdbid": item.get("tmdbid"),
            "doubanid": str(doubanid).strip() if doubanid else None,
            "bangumiid": item.get("bangumiid"),
            "title": str(title).strip() if title else None,
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }

    @staticmethod
    def _serialize_item(item: dict[str, Any]) -> str:
        """
        序列化共享识别项
        """
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _deserialize_item(payload: Optional[str]) -> Optional[dict[str, Any]]:
        """
        反序列化共享识别项
        """
        if not payload:
            return None

        try:
            item = json.loads(payload)
        except Exception as err:
            logger.warning(f"解析共享媒体识别缓存失败: {err}")
            return None

        return item if isinstance(item, dict) else None

    async def start(self):
        """
        启动共享媒体识别服务
        """
        if self._started:
            return

        self._started = True

    async def stop(self):
        """
        停止共享媒体识别服务
        """
        self._started = False

    @staticmethod
    def _merge_item(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        """
        合并共享识别项，新值优先，空值不覆盖已有值
        """
        merged = dict(existing)
        for key, value in incoming.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        return merged

    async def _get_item(self, cache_key: Optional[str]) -> Optional[dict[str, Any]]:
        """
        按缓存键读取共享识别项
        """
        if not cache_key:
            return None

        raw_item = await get_redis().get(self._item_key(cache_key))
        return self._deserialize_item(raw_item)

    async def upsert(self, item: MediaRecognizeShareItem) -> dict[str, Any]:
        """
        新增或更新共享识别记录
        """
        normalized_item = self._normalize_item_dict(item.model_dump())
        if not normalized_item:
            return {"code": 1, "message": "关键字或媒体类型无效"}

        if not any(
                [
                    normalized_item.get("tmdbid"),
                    normalized_item.get("doubanid"),
                    normalized_item.get("bangumiid"),
                ]
        ):
            return {"code": 1, "message": "至少需要一个有效的媒体ID"}

        cache_key = self._build_item_cache_key(
            keyword=normalized_item.get("keyword"),
            media_type=normalized_item.get("type"),
            year=normalized_item.get("year"),
            season=normalized_item.get("season"),
        )
        if not cache_key:
            return {"code": 1, "message": "无法生成缓存键"}

        redis = get_redis()
        existing = await self._get_item(cache_key) or {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        normalized_item["created_at"] = existing.get("created_at") or now
        normalized_item["updated_at"] = now
        merged_item = self._merge_item(existing, normalized_item)

        await redis.set(self._item_key(cache_key), self._serialize_item(merged_item))

        return {
            "code": 0,
            "message": "success",
            "data": {"item": merged_item},
        }

    async def query(
            self,
            keyword: str,
            media_type: Optional[str] = None,
            year: Optional[str] = None,
            season: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        查询共享识别记录
        """
        keyword_key = self._normalize_keyword(keyword)
        if not keyword_key:
            return {"code": 1, "message": "关键字不能为空"}

        type_key = self._normalize_media_type(media_type)
        if not type_key:
            return {"code": 1, "message": "媒体类型不能为空"}

        year_key = self._normalize_year(year)
        season_key = self._normalize_season(type_key, season)
        exact_key = self._build_item_cache_key(
            keyword=keyword_key,
            media_type=type_key,
            year=year_key,
            season=season_key,
        )
        item = await self._get_item(exact_key)
        if not item:
            return {"code": 1, "message": "未找到共享识别记录"}

        return {
            "code": 0,
            "message": "success",
            "data": {"item": item},
        }


media_recognize_share_service = MediaRecognizeShareService()
