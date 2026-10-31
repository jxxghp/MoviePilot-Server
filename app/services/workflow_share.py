"""
工作流分享服务
"""
from datetime import datetime
from typing import Dict, Any, List

from app.models import WorkflowShare
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.core.share_token import (
    create_share_owner_token,
    hash_share_owner_token,
    verify_share_owner_token,
)
from app.schemas.models import WorkflowShareItem


class WorkflowShareService:
    """工作流分享服务类"""

    @staticmethod
    async def create_share(db: AsyncSession, workflow: WorkflowShareItem) -> Dict[str, Any]:
        """新增工作流分享"""
        if not workflow.share_title or not workflow.share_user:
            return {
                "success": False,
                "message": "请填写分享标题和分享人",
                "data": None,
            }

        # 查询数据库中是否存在
        share = await WorkflowShare.read(db, title=workflow.share_title, user=workflow.share_user)

        # 如果不存在则创建
        if not share:
            workflow.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            owner_token = create_share_owner_token()
            share = WorkflowShare(
                **workflow.model_dump(),
                count=0,
                owner_token_hash=hash_share_owner_token(owner_token),
            )
            await share.create(db)
        # 如果存在则报错
        else:
            return {
                "success": False,
                "message": "您使用的昵称已经分享过这个工作流了",
                "data": None,
            }

        # 清除缓存
        cache_manager.workflow_share_cache.clear()

        return {
            "success": True,
            "message": "success",
            "data": {"id": share.id, "owner_token": owner_token},
        }

    @staticmethod
    async def delete_share(db: AsyncSession, share_id: int, owner_token: str | None) -> Dict[str, Any]:
        """删除工作流分享"""
        # 查询数据库中是否存在
        share = await WorkflowShare.read_by_id(db, share_id)

        # 公开列表中的 share_uid 不是授权凭据，只接受创建时返回的私有令牌。
        if share and verify_share_owner_token(owner_token, share.owner_token_hash):
            await share.delete(db, share_id)
            # 清除缓存
            cache_manager.workflow_share_cache.clear()
            return {"success": True, "message": "success", "data": None}

        return {"success": False, "message": "分享不存在或无权限", "data": None}

    @staticmethod
    async def get_shares(db: AsyncSession, name: str = None, page: int = 1, count: int = 30) -> List[Dict[str, Any]]:
        """查询分享的工作流"""
        cache_key = f"workflow_{name}_{page}_{count}"
        cached_data = cache_manager.workflow_share_cache.get(cache_key)

        if cached_data is None:
            shares = await WorkflowShare.list(db, name=name, page=page, count=count)
            cached_data = []
            for sha in shares:
                item = sha.dict()
                # 历史数据中可能残留分享方写入的执行上下文，对外一律不下发
                item["context"] = None
                cached_data.append(item)
            cache_manager.workflow_share_cache.set(cache_key, cached_data)

        return cached_data

    @staticmethod
    async def fork_share(db: AsyncSession, share_id: int) -> Dict[str, Any]:
        """复用分享的工作流"""
        await WorkflowShare.increment_count_by_id(db, sid=share_id)
        await db.commit()

        return {"code": 0, "message": "success"}
