"""
知识中心后台维护路由，复用现有会话、角色、CSRF 和审计。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminIdentity, administrator
from app.core.config import settings
from app.db.deps import get_db
from app.schemas.knowledge import (
    CollectionPayload, CollectionUpdate, DocumentPayload, RevisionCreate,
    SitePayload, SiteUpdate, SourcePayload, SourceUpdate,
)
from app.services.knowledge import catalog, documents


async def no_store(response: Response) -> None:
    """
    私有知识管理响应不进入浏览器或共享代理缓存。
    """
    response.headers["Cache-Control"] = "no-store"


class KnowledgeRoute(APIRoute):
    """
    知识表单验证失败不回显原输入，防止误填的 Cookie 或密码被错误响应带回。
    """

    def get_route_handler(self):
        """
        只替换知识路由的参数错误，不改变其他后台接口的异常合同。
        """
        handler = super().get_route_handler()

        async def validate(request: Request) -> Response:
            """
            保留真实鉴权与请求处理，将包含输入的验证详情转换为安全提示。
            """
            try:
                return await handler(request)
            except RequestValidationError as error:
                raise HTTPException(422, "知识表单字段无效，请检查来源地址、版本和必填项") from error

        return validate


router = APIRouter(prefix="/knowledge", tags=["admin-knowledge"], route_class=KnowledgeRoute,
                   dependencies=[Depends(administrator), Depends(no_store)])


@router.get("/status")
async def status():
    """
    区分可维护草稿与尚未启用的在线知识服务，不展示来源正文。
    """
    return {"enabled": settings.KNOWLEDGE_ENABLED, "protocol_version": 1}


@router.get("/collections")
async def collections(q: str = Query("", max_length=120), page: int = Query(1, ge=1),
                      size: int = Query(30, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    """
    管理员分页查看知识集合。
    """
    return await catalog.list_items(db, "collections", q, page, size)


@router.post("/collections", status_code=201)
async def create_collection(payload: CollectionPayload, identity: AdminIdentity = Depends(administrator),
                            db: AsyncSession = Depends(get_db)):
    """
    新建集合，不自动授予实例访问权限。
    """
    return await catalog.create_collection(db, payload, identity.user.username)


@router.put("/collections/{item_id}")
async def update_collection(item_id: str, payload: CollectionUpdate, identity: AdminIdentity = Depends(administrator),
                            db: AsyncSession = Depends(get_db)):
    """
    按并发版本更新集合，保留稳定身份。
    """
    return await catalog.update_collection(db, item_id, payload, identity.user.username)


@router.get("/sites")
async def sites(q: str = Query("", max_length=120), page: int = Query(1, ge=1),
                size: int = Query(30, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    """
    分页展示站点及无歧义别名。
    """
    return await catalog.list_items(db, "sites", q, page, size)


@router.post("/sites", status_code=201)
async def create_site(payload: SitePayload, identity: AdminIdentity = Depends(administrator),
                      db: AsyncSession = Depends(get_db)):
    """
    新建站点，重复名称或别名时整体拒绝。
    """
    return await catalog.create_site(db, payload, identity.user.username)


@router.put("/sites/{item_id}")
async def update_site(item_id: str, payload: SiteUpdate, identity: AdminIdentity = Depends(administrator),
                      db: AsyncSession = Depends(get_db)):
    """
    带版本校验地维护站点与别名。
    """
    return await catalog.update_site(db, item_id, payload, identity.user.username)


@router.get("/sources")
async def sources(q: str = Query("", max_length=120), page: int = Query(1, ge=1),
                  size: int = Query(30, ge=1, le=100), collection_id: str | None = Query(None, max_length=32),
                  db: AsyncSession = Depends(get_db)):
    """
    分页查看指定集合内的来源配置，不返回任何采集秘密。
    """
    return await catalog.list_items(db, "sources", q, page, size, collection_id)


@router.post("/sources", status_code=201)
async def create_source(payload: SourcePayload, identity: AdminIdentity = Depends(administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    保存来源配置，不在请求中抓网页或创建周期任务。
    """
    return await catalog.create_source(db, payload, identity.user.username)


@router.put("/sources/{item_id}")
async def update_source(item_id: str, payload: SourceUpdate, identity: AdminIdentity = Depends(administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    更新来源白名单字段，防止改变已有文档的集合归属。
    """
    return await catalog.update_source(db, item_id, payload, identity.user.username)


@router.get("/documents")
async def document_list(q: str = Query("", max_length=120), page: int = Query(1, ge=1),
                        size: int = Query(30, ge=1, le=100), source_id: str | None = Query(None, max_length=32),
                        db: AsyncSession = Depends(get_db)):
    """
    分页列出最新修订摘要，列表不包含正文。
    """
    return await documents.list_documents(db, q, page, size, source_id)


@router.post("/documents", status_code=201)
async def create_document(payload: DocumentPayload, identity: AdminIdentity = Depends(administrator),
                          db: AsyncSession = Depends(get_db)):
    """
    在手工来源中创建尚未发布的知识草稿。
    """
    return await documents.create_document(db, payload, identity.user.username)


@router.get("/documents/{item_id}")
async def read_document(item_id: str, revision: int | None = Query(None, ge=1),
                        db: AsyncSession = Depends(get_db)):
    """
    管理员读取最新或指定历史修订。
    """
    return await documents.read_document(db, item_id, revision)


@router.post("/documents/{item_id}/revisions", status_code=201)
async def append_revision(item_id: str, payload: RevisionCreate, identity: AdminIdentity = Depends(administrator),
                          db: AsyncSession = Depends(get_db)):
    """
    追加新草稿，不原地修改旧版或自动发布。
    """
    return await documents.append_revision(db, item_id, payload, identity.user.username)
