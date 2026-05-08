"""
共享媒体识别相关API路由
"""
from fastapi import APIRouter

from app.schemas.models import MediaRecognizeShareItem
from app.services.media_recognize_share import media_recognize_share_service

router = APIRouter()


@router.post("/share")
async def upsert_media_recognize_share(item: MediaRecognizeShareItem):
    """
    新增或更新共享媒体识别记录
    """
    return await media_recognize_share_service.upsert(item)


@router.get("/share")
async def query_media_recognize_share(
        keyword: str,
        type: str = None,
        year: str = None,
        season: int = None,
):
    """
    查询共享媒体识别记录
    """
    return await media_recognize_share_service.query(
        keyword=keyword,
        media_type=type,
        year=year,
        season=season,
    )
