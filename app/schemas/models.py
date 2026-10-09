"""
Pydantic模型定义
"""
from typing import Any, ClassVar, List, Optional
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.media import (
    MediaSource,
    resolve_legacy_media_identity,
    resolve_media_identity,
)


class SortType(str, Enum):
    """排序类型枚举"""
    TIME = "time"  # 按时间排序
    COUNT = "count"  # 按人数排序
    RATING = "rating"  # 按评分排序


class PluginStatisticItem(BaseModel):
    """插件统计项"""
    plugin_id: str
    repo_url: Optional[str] = None


class PluginStatisticList(BaseModel):
    """插件统计列表"""
    plugins: List[PluginStatisticItem]


class PluginRatingItem(BaseModel):
    """插件评分请求"""
    rating: float = Field(ge=0.1, le=5.0, multiple_of=0.1)


class PluginRatingResult(BaseModel):
    """插件评分结果"""
    plugin_id: str
    average_rating: float = 0.0
    rating_count: int = 0
    user_rating: Optional[float] = None


class UsageStatisticItem(BaseModel):
    """安装版本统计项"""
    user_uid: Optional[str] = None
    backend_version: Optional[str] = None
    frontend_version: Optional[str] = None
    version_flag: Optional[str] = None
    platform: Optional[str] = None
    arch: Optional[str] = None


class MediaIdentityCompatibilityModel(BaseModel):
    """统一媒体身份请求基类，并在 API 边界兼容旧版客户端字段。"""

    LEGACY_IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "tmdbid", "doubanid", "bangumiid", "anilistid", "imdbid",
        "tvdbid", "mediaid", "source",
    })
    model_config = ConfigDict(extra="ignore")

    media_source: Optional[MediaSource] = None
    media_id: Optional[str] = None
    tmdbid: Optional[str | int] = Field(default=None, exclude=True)
    doubanid: Optional[str | int] = Field(default=None, exclude=True)
    bangumiid: Optional[str | int] = Field(default=None, exclude=True)
    anilistid: Optional[str | int] = Field(default=None, exclude=True)
    imdbid: Optional[str | int] = Field(default=None, exclude=True)
    tvdbid: Optional[str | int] = Field(default=None, exclude=True)
    mediaid: Optional[str] = Field(default=None, exclude=True)
    source: Optional[str] = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_compatible_identity(cls, data: Any) -> Any:
        """将旧版身份输入转换为内置或插件扩展来源和原生 ID。"""
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        source, media_id = resolve_legacy_media_identity(normalized)
        if source and media_id:
            normalized["media_source"] = source
            normalized["media_id"] = media_id
        return normalized

    @model_validator(mode="after")
    def normalize_identity_pair(self):
        """确保模型内部的统一身份始终完整成对，零哨兵按未提供处理。"""
        source, media_id = resolve_media_identity(self)
        self.media_source = source
        self.media_id = media_id
        return self

    def storage_payload(self) -> dict[str, Any]:
        """返回只含统一身份与业务字段的持久化载荷。"""
        return self.model_dump(exclude=self.LEGACY_IDENTITY_FIELDS)


class SubscribeStatisticItem(MediaIdentityCompatibilityModel):
    """订阅统计项"""

    name: Optional[str] = None
    year: Optional[str] = None
    type: Optional[str] = None
    music_type: Optional[str] = None
    total_tracks: Optional[int] = None
    genre_ids: Optional[str] = None
    season: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    vote: Optional[float] = None
    description: Optional[str] = None


class SubscribeStatisticList(BaseModel):
    """订阅统计列表"""
    subscribes: List[SubscribeStatisticItem]


class SubscribeShareItem(MediaIdentityCompatibilityModel):
    """订阅分享项"""

    id: Optional[int] = None
    share_title: Optional[str] = None
    share_comment: Optional[str] = None
    share_user: Optional[str] = None
    share_uid: Optional[str] = None
    name: Optional[str] = None
    year: Optional[str] = None
    type: Optional[str] = None
    keyword: Optional[str] = None
    music_type: Optional[str] = None
    total_tracks: Optional[int] = None
    season: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    vote: Optional[float] = None
    description: Optional[str] = None
    genre_ids: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    quality: Optional[str] = None
    resolution: Optional[str] = None
    effect: Optional[str] = None
    total_episode: Optional[int] = None
    custom_words: Optional[str] = None
    media_category: Optional[str] = None
    episode_group: Optional[str] = None
    date: Optional[str] = None


class WorkflowShareItem(BaseModel):
    """工作流分享项"""
    id: Optional[int] = None
    share_title: Optional[str] = None
    share_comment: Optional[str] = None
    share_user: Optional[str] = None
    share_uid: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    timer: Optional[str] = None
    actions: Optional[str] = None  # JSON字符串
    flows: Optional[str] = None  # JSON字符串
    # 不接收 context：执行上下文是分享者本机的运行状态，复用方用不上，且内容不可信
    date: Optional[str] = None


class SubscribeShareStatisticItem(BaseModel):
    """订阅分享统计项"""
    share_user: str
    share_count: int
    total_reuse_count: int


class MediaRecognizeShareItem(MediaIdentityCompatibilityModel):
    """共享媒体识别项"""

    keyword: str
    type: str
    year: Optional[str] = None
    season: Optional[int] = None
    music_type: Optional[str] = None
    title: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ResponseModel(BaseModel):
    """通用响应模型"""
    code: int
    message: str
    data: Optional[dict] = None
