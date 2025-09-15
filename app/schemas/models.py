"""
Pydantic模型定义
"""
from typing import List, Optional
from pydantic import BaseModel


class PluginStatisticItem(BaseModel):
    """插件统计项"""
    plugin_id: str


class PluginStatisticList(BaseModel):
    """插件统计列表"""
    plugins: List[PluginStatisticItem]


class SubscribeStatisticItem(BaseModel):
    """订阅统计项"""
    name: Optional[str] = None
    year: Optional[str] = None
    type: Optional[str] = None
    tmdbid: Optional[int] = None
    imdbid: Optional[str] = None
    tvdbid: Optional[int] = None
    doubanid: Optional[str] = None
    season: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    vote: Optional[float] = None
    description: Optional[str] = None


class SubscribeStatisticList(BaseModel):
    """订阅统计列表"""
    subscribes: List[SubscribeStatisticItem]


class SubscribeShareItem(BaseModel):
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
    tmdbid: Optional[int] = None
    imdbid: Optional[str] = None
    tvdbid: Optional[int] = None
    doubanid: Optional[str] = None
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
    context: Optional[str] = None  # JSON字符串
    date: Optional[str] = None


class SubscribeShareStatisticItem(BaseModel):
    """订阅分享统计项"""
    share_user: str
    share_count: int
    total_reuse_count: int


class ResponseModel(BaseModel):
    """通用响应模型"""
    code: int
    message: str
    data: Optional[dict] = None