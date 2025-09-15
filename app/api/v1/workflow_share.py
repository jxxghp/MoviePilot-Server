"""
工作流分享相关API路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.deps import get_db
from app.schemas.models import WorkflowShareItem
from app.services.workflow_share import WorkflowShareService

router = APIRouter()


@router.post("/share")
async def workflow_share(workflow: WorkflowShareItem, db: AsyncSession = Depends(get_db)):
    """
    新增工作流分享
    """
    return await WorkflowShareService.create_share(db, workflow)


@router.delete("/share/{sid}")
async def workflow_share_delete(sid: int, share_uid: str, db: AsyncSession = Depends(get_db)):
    """
    删除工作流分享
    """
    return await WorkflowShareService.delete_share(db, sid, share_uid)


@router.get("/shares")
async def workflow_shares(name: str = None, page: int = 1, count: int = 30, db: AsyncSession = Depends(get_db)):
    """
    查询分享的工作流
    """
    return await WorkflowShareService.get_shares(db, name, page, count)


@router.get("/fork/{shareid}")
async def workflow_fork(shareid: int, db: AsyncSession = Depends(get_db)):
    """
    复用分享的工作流
    """
    return await WorkflowShareService.fork_share(db, shareid)