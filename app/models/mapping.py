"""
持久化跨来源到 TMDB 的共享关系，独立于识别缓存和定期清理。
"""

from sqlalchemy import Boolean, CheckConstraint, Column, Integer, String, UniqueConstraint

from app.models.base import Base, get_id_column


class MediaMapping(Base):
    """
    按原来源、原生 ID、类型和季号唯一保存 TMDB 关系。

    未指定原季号使用 -1，避免数据库 NULL 唯一约束允许重复行；停用行作为撤销标记保留。
    """

    __tablename__ = "MEDIA_MAPPING"
    __table_args__ = (
        UniqueConstraint("media_source", "media_id", "type", "season", name="uq_media_mapping_identity"),
        CheckConstraint("type IN ('movie', 'tv') AND tmdb_type IN ('movie', 'tv')", name="ck_media_mapping_types"),
        CheckConstraint("tmdb_id > 0 AND season >= -1 AND (tmdb_season IS NULL OR tmdb_season >= 0)", name="ck_media_mapping_ids"),
        CheckConstraint("media_source <> 'themoviedb' AND media_source <> '' AND media_id <> '' AND media_id <> '0'", name="ck_media_mapping_source"),
        CheckConstraint("(type <> 'movie' OR season = -1) AND (tmdb_type <> 'movie' OR tmdb_season IS NULL)", name="ck_media_mapping_seasons"),
    )

    id = get_id_column()
    media_source = Column(String(64), nullable=False, comment="非 TMDB 原来源")
    media_id = Column(String(256), nullable=False, comment="来源原生 ID")
    type = Column(String(8), nullable=False, comment="原来源影视类型")
    season = Column(Integer, nullable=False, default=-1, comment="原来源季号，-1 表示未指定")
    tmdb_id = Column(Integer, nullable=False, index=True, comment="TMDB ID")
    tmdb_type = Column(String(8), nullable=False, comment="TMDB 影视类型")
    tmdb_season = Column(Integer, nullable=True, comment="TMDB 季号")
    blocked = Column(Boolean, nullable=False, default=False, comment="后台撤销标记，公共上报不能覆盖")
    created_at = Column(String, nullable=False, comment="首次共享时间")
    updated_at = Column(String, nullable=False, comment="最近管理修改时间")
