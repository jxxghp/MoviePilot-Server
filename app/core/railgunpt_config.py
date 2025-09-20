"""
RailgunPT 站点配置文件
参考13city配置格式
"""
from typing import Dict, Any

# RailgunPT 站点配置
RAILGUNPT_SITE_CONFIG: Dict[str, Any] = {
    "name": "RailgunPT",
    "url": "https://bilibili.download/",
    "type": "NexusPHP",
    "description": "RailgunPT 站点配置",
    "enabled": True,
    "categories": {
        401: {
            "id": 401,
            "name": "电影",
            "name_en": "Movies",
            "description": "电影资源分类"
        },
        402: {
            "id": 402,
            "name": "电视剧",
            "name_en": "TV Series",
            "description": "电视剧资源分类"
        },
        403: {
            "id": 403,
            "name": "综艺",
            "name_en": "TV Shows",
            "description": "综艺节目资源分类"
        },
        404: {
            "id": 404,
            "name": "纪录片",
            "name_en": "Documentaries",
            "description": "纪录片资源分类"
        },
        405: {
            "id": 405,
            "name": "动漫",
            "name_en": "Animations",
            "description": "动漫资源分类"
        }
    },
    "nexusphp_config": {
        "enabled": True,
        "api_endpoint": "/api.php",
        "rss_endpoint": "/rss.php",
        "search_endpoint": "/torrents.php",
        "user_agent": "RailgunPT-Client/1.0",
        "timeout": 30,
        "retry_count": 3
    }
}

# 站点配置列表
SITE_CONFIGS = {
    "railgunpt": RAILGUNPT_SITE_CONFIG
}

def get_site_config(site_name: str) -> Dict[str, Any]:
    """获取站点配置"""
    return SITE_CONFIGS.get(site_name.lower(), {})

def get_site_categories(site_name: str) -> Dict[int, Dict[str, Any]]:
    """获取站点分类配置"""
    config = get_site_config(site_name)
    return config.get("categories", {})

def is_site_enabled(site_name: str) -> bool:
    """检查站点是否启用"""
    config = get_site_config(site_name)
    return config.get("enabled", False)

def get_nexusphp_config(site_name: str) -> Dict[str, Any]:
    """获取NexusPHP配置"""
    config = get_site_config(site_name)
    return config.get("nexusphp_config", {})