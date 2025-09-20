"""
站点配置管理
"""
from typing import Dict, List, Optional
from pydantic import BaseModel


class SiteCategory(BaseModel):
    """站点分类配置"""
    id: int
    name: str
    name_en: str


class SiteConfig(BaseModel):
    """站点配置"""
    name: str
    url: str
    type: str  # NexusPHP, Gazelle, etc.
    categories: List[SiteCategory]
    enabled: bool = True
    description: Optional[str] = None


# RailgunPT 站点配置
RAILGUNPT_CONFIG = SiteConfig(
    name="RailgunPT",
    url="https://bilibili.download/",
    type="NexusPHP",
    description="RailgunPT 站点配置",
    categories=[
        SiteCategory(id=401, name="电影", name_en="Movies"),
        SiteCategory(id=402, name="电视剧", name_en="TV Series"),
        SiteCategory(id=403, name="综艺", name_en="TV Shows"),
        SiteCategory(id=404, name="纪录片", name_en="Documentaries"),
        SiteCategory(id=405, name="动漫", name_en="Animations"),
    ]
)

# 站点配置字典
SITE_CONFIGS: Dict[str, SiteConfig] = {
    "railgunpt": RAILGUNPT_CONFIG,
}


def get_site_config(site_name: str) -> Optional[SiteConfig]:
    """获取站点配置"""
    return SITE_CONFIGS.get(site_name.lower())


def get_all_site_configs() -> Dict[str, SiteConfig]:
    """获取所有站点配置"""
    return SITE_CONFIGS


def get_site_categories(site_name: str) -> List[SiteCategory]:
    """获取站点分类"""
    config = get_site_config(site_name)
    if config:
        return config.categories
    return []