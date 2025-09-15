"""
工作流分享服务
"""
from datetime import datetime
from typing import Dict, Any, List

from app.models import WorkflowShare
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.schemas.models import WorkflowShareItem


class WorkflowShareService:
    """工作流分享服务类"""

    @staticmethod
    async def create_share(db: AsyncSession, workflow: WorkflowShareItem) -> Dict[str, Any]:
        """新增工作流分享"""
        if not workflow.share_title or not workflow.share_user:
            return {
                "code": 1,
                "message": "请填写分享标题和分享人"
            }

        # 查询数据库中是否存在
        share = await WorkflowShare.read(db, title=workflow.share_title, user=workflow.share_user)

        # 如果不存在则创建
        if not share:
            workflow.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            share = WorkflowShare(**workflow.model_dump(), count=0)
            await share.create(db)
        # 如果存在则报错
        else:
            return {
                "code": 2,
                "message": "您使用的昵称已经分享过这个工作流了"
            }

        # 清除缓存
        cache_manager.workflow_share_cache.clear()

        return {"code": 0, "message": "success"}

    @staticmethod
    async def delete_share(db: AsyncSession, share_id: int, share_uid: str) -> Dict[str, Any]:
        """删除工作流分享"""
        # 查询数据库中是否存在
        share = await WorkflowShare.read_by_id(db, share_id)

        # 如果存在则删除
        if share and share_uid:
            await share.delete(db, share_id)
            # 清除缓存
            cache_manager.workflow_share_cache.clear()
            return {"code": 0, "message": "success"}

        return {"code": 1, "message": "分享不存在或无权限"}

    @staticmethod
    async def get_shares(db: AsyncSession, name: str = None, page: int = 1, count: int = 30) -> List[Dict[str, Any]]:
        """查询分享的工作流"""
        cache_key = f"workflow_{name}_{page}_{count}"
        cached_data = cache_manager.workflow_share_cache.get(cache_key)

        if not cached_data:
            shares = await WorkflowShare.list(db, name=name, page=page, count=count)
            cached_data = [sha.dict() for sha in shares]
            cache_manager.workflow_share_cache.set(cache_key, cached_data)

        return cached_data

    @staticmethod
    async def fork_share(db: AsyncSession, share_id: int) -> Dict[str, Any]:
        """复用分享的工作流"""
        # 查询数据库中是否存在
        share = await WorkflowShare.read_by_id(db, sid=share_id)

        # 如果存在则更新
        if share:
            await share.update(db, {"count": share.count + 1})

        return {"code": 0, "message": "success"}
