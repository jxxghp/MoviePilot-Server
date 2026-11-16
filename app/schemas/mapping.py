"""
非 TMDB 影视来源与 TMDB 的单向对应关系协议。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.media import MediaSource, normalize_media_source


class MediaMappingQuery(BaseModel):
    """
    按来源原生身份查询；未指定季号时只返回无歧义的关系。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    media_source: MediaSource = Field(description="非 TMDB 的影视来源")
    media_id: str = Field(min_length=1, max_length=256, description="来源原生媒体 ID")
    type: Literal["movie", "tv"] | None = Field(default=None, description="原来源的影视类型")
    season: int | None = Field(default=None, ge=0, le=10000, description="转换时的原来源季号；未指定为 null")

    @field_validator("media_source", mode="before")
    @classmethod
    def normalize_source(cls, value):
        """
        规范内置别名和插件来源标识。
        """
        source = normalize_media_source(value)
        if source is None or source == MediaSource.TMDB:
            raise ValueError("仅支持非 TMDB 来源到 TMDB 的关系")
        return source

    @field_validator("media_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """
        拒绝旧版零哨兵，保留原生 ID 的大小写。
        """
        if value == "0":
            raise ValueError("媒体 ID 无效")
        return value


class MediaMapping(MediaMappingQuery):
    """
    保存转换前身份和转换后的 TMDB 类型、ID、季号。
    """

    type: Literal["movie", "tv"] = Field(description="原来源的影视类型")
    tmdb_id: int = Field(gt=0, le=2147483647, description="TMDB 原生 ID")
    tmdb_type: Literal["movie", "tv"] = Field(description="TMDB 电影或电视剧类型")
    tmdb_season: int | None = Field(default=None, ge=0, le=10000, description="TMDB 剧集季号，电影或未知为 null")

    @model_validator(mode="after")
    def validate_seasons(self):
        """
        电影不携带季号，避免形成错误的分季直达链接。
        """
        if self.type == "movie" and self.season is not None:
            raise ValueError("电影来源不能指定季号")
        if self.tmdb_type == "movie" and self.tmdb_season is not None:
            raise ValueError("TMDB 电影不能指定季号")
        return self
