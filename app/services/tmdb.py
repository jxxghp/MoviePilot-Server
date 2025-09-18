"""
TheMovieDB API服务
"""
from typing import Dict, Any, Optional

import aiohttp

from app.core.config import settings


class TMDBService:
    """TheMovieDB API服务类"""

    def __init__(self):
        self.api_key = settings.TMDB_API_KEY
        self.base_url = settings.TMDB_API_URL
        self.timeout = settings.TMDB_TIMEOUT

    async def _make_request(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """发起API请求"""
        try:
            async with session.get(url, timeout=self.timeout) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"TMDB API请求失败: {response.status} {url}")
                    return None
        except Exception as e:
            print(f"TMDB API请求异常: {e}")
            return None

    async def get_movie_details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """获取电影详情"""
        if not self.api_key or not tmdb_id:
            return None

        url = f"{self.base_url}/movie/{tmdb_id}?api_key={self.api_key}&language=zh-CN"

        async with aiohttp.ClientSession() as session:
            return await self._make_request(session, url)

    async def get_tv_details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """获取电视剧详情"""
        if not self.api_key or not tmdb_id:
            return None

        url = f"{self.base_url}/tv/{tmdb_id}?api_key={self.api_key}&language=zh-CN"

        async with aiohttp.ClientSession() as session:
            return await self._make_request(session, url)

    async def get_media_details(self, tmdb_id: int, media_type: str = "movie") -> Optional[Dict[str, Any]]:
        """获取媒体详情（电影或电视剧）"""
        if media_type.lower() == "tv":
            return await self.get_tv_details(tmdb_id)
        else:
            return await self.get_movie_details(tmdb_id)

    async def get_genre_ids(self, tmdb_id: int, media_type: str = "movie") -> Optional[str]:
        """获取媒体的genre_ids"""
        details = await self.get_media_details(tmdb_id, media_type)
        if details and "genres" in details:
            genre_ids = [str(genre["id"]) for genre in details["genres"]]
            return ",".join(genre_ids)
        return None

    async def get_media_info(self, tmdb_id: int, media_type: str = "movie") -> Optional[Dict[str, Any]]:
        """获取媒体完整信息，包括genre_ids"""
        details = await self.get_media_details(tmdb_id, media_type)
        if not details:
            return None

        # 提取需要的信息
        info = {
            "name": details.get("title") or details.get("name"),
            "year": details.get("release_date", "").split("-")[0] if details.get("release_date") else
            details.get("first_air_date", "").split("-")[0] if details.get("first_air_date") else None,
            "type": media_type,
            "tmdbid": tmdb_id,
            "poster": f"https://image.tmdb.org/t/p/w500{details.get('poster_path')}" if details.get(
                "poster_path") else None,
            "backdrop": f"https://image.tmdb.org/t/p/w1280{details.get('backdrop_path')}" if details.get(
                "backdrop_path") else None,
            "vote": details.get("vote_average"),
            "description": details.get("overview"),
            "genre_ids": ",".join([str(genre["id"]) for genre in details.get("genres", [])])
        }

        return info


# 全局TMDB服务实例
tmdb_service = TMDBService()
