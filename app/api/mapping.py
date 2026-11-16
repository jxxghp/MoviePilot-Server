"""
非 TMDB 来源到 TMDB 的公共共享接口。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.mapping import MediaMapping, MediaMappingQuery
from app.services.mapping import MediaMappingService

router = APIRouter()


@router.get("/mapping")
async def query_mapping(query: Annotated[MediaMappingQuery, Query()], response: Response,
                        db: AsyncSession = Depends(get_db)):
    """
    查询已有关系，不调用外部媒体源或现场做名称匹配。
    """
    response.headers["Cache-Control"] = "no-store"
    return {"code": 0, "data": {"item": await MediaMappingService.query(db, query)}}


@router.post("/mapping")
async def share_mapping(item: MediaMapping, response: Response, db: AsyncSession = Depends(get_db)):
    """
    只共享公开媒体身份，不接收用户信息、令牌或媒体详情。
    """
    response.headers["Cache-Control"] = "no-store"
    return {"code": 0, "data": {"item": await MediaMappingService.share(db, item)}}
