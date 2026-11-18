"""
知识中心后台维护路由，复用现有会话、角色、CSRF 和审计。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminIdentity, administrator, super_administrator
from app.admin.audit import record
from app.core.config import settings
from app.db.deps import get_db
from app.models.knowledge import KnowledgeImport, KnowledgeJob, KnowledgeState
from app.schemas.knowledge import (
    ClientPayload, ClientUpdate, CollectionPayload, CollectionUpdate, CredentialRotation, DocumentPayload, RevisionCreate,
    ImportApproval, ImportPreflight, KnowledgePayload, PublicationAction,
    SitePayload, SiteUpdate, SourcePayload, SourceUpdate, SourceCredentialAction, SourceCredentialPayload, VersionAction,
)
from app.services.knowledge import access, catalog, documents, jobs
from app.services.knowledge import indexes
from app.services.knowledge.imports import catalog as import_catalog, files as import_files
from app.services.knowledge.credentials import catalog as credential_catalog


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
            except RequestValidationError:
                raise HTTPException(422, "知识表单字段无效，请检查来源地址、版本和必填项",
                                    headers={"Cache-Control": "no-store"}) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise

        return validate


router = APIRouter(prefix="/knowledge", tags=["admin-knowledge"], route_class=KnowledgeRoute,
                   dependencies=[Depends(administrator), Depends(no_store)])


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    """
    区分可维护草稿与尚未启用的在线知识服务，不展示来源正文。
    """
    state = await db.get(KnowledgeState, "main")
    return {"enabled": settings.KNOWLEDGE_ENABLED, "protocol_version": 1, "index": {
        "generation": state.generation if state else 0,
        "active_generation": state.active_generation if state else 0,
        "has_snapshot": bool(state and state.active_snapshot),
    }}


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


@router.get("/sources/{item_id}/credential")
async def source_credential(item_id: str, db: AsyncSession = Depends(get_db)):
    """查看来源的凭据配置状态，任何角色都不会取得秘密或密文。"""
    return await credential_catalog.read(db, item_id)


@router.put("/sources/{item_id}/credential")
async def replace_source_credential(item_id: str, payload: SourceCredentialPayload,
                                    identity: AdminIdentity = Depends(super_administrator),
                                    db: AsyncSession = Depends(get_db)):
    """只有超级管理员可明确替换绑定范围内的认证值。"""
    return await credential_catalog.save(db, item_id, payload, identity.user.username)


@router.post("/sources/{item_id}/credential/clear")
async def clear_source_credential(item_id: str, payload: SourceCredentialAction,
                                  identity: AdminIdentity = Depends(super_administrator),
                                  db: AsyncSession = Depends(get_db)):
    """明确删除认证密文并使旧来源版本失效，保留不含秘密的审计。"""
    return await credential_catalog.clear(db, item_id, payload, identity.user.username)


@router.post("/sources/{item_id}/credential/rewrap")
async def rewrap_source_credential(item_id: str, payload: SourceCredentialAction,
                                   identity: AdminIdentity = Depends(super_administrator),
                                   db: AsyncSession = Depends(get_db)):
    """使用部署 active 密钥重新包封认证值，无须向后台浏览器返回明文。"""
    return await credential_catalog.rewrap(db, item_id, payload, identity.user.username)


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


@router.get("/clients", dependencies=[Depends(super_administrator)])
async def clients(q: str = Query("", max_length=120), page: int = Query(1, ge=1),
                  size: int = Query(30, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    """
    只有超级管理员可以分页查看独立客户端与集合授权。
    """
    return await access.list_clients(db, q, page, size)


@router.get("/clients/{item_id}", dependencies=[Depends(super_administrator)])
async def read_client(item_id: str, db: AsyncSession = Depends(get_db)):
    """
    超级管理员刷新客户端版本；历史明文令牌无法读取。
    """
    return await access.read_client(db, item_id)


@router.post("/clients", status_code=201)
async def create_client(payload: ClientPayload, identity: AdminIdentity = Depends(super_administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    中心签发新客户端，令牌仅在成功创建时显示一次。
    """
    return await access.create_client(db, payload, identity.user.username)


@router.put("/clients/{item_id}")
async def update_client(item_id: str, payload: ClientUpdate, identity: AdminIdentity = Depends(super_administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    超级管理员按版本维护启停、到期时间和完整集合授权。
    """
    return await access.update_client(db, item_id, payload, identity.user.username)


@router.post("/clients/{item_id}/rotate")
async def rotate_client(item_id: str, payload: CredentialRotation, identity: AdminIdentity = Depends(super_administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    轮换后旧令牌立即影响后续请求，新令牌只在本次成功响应中出现。
    """
    return await access.rotate_client(db, item_id, payload.expected_version, identity.user.username)


@router.post("/documents/{item_id}/publish")
async def publish_document(item_id: str, payload: PublicationAction, identity: AdminIdentity = Depends(administrator),
                           db: AsyncSession = Depends(get_db)):
    """
    明确批准所审阅的修订并返回索引任务，不将排队结果展示为可检索。
    """
    return await documents.publish_document(db, item_id, payload.expected_version, payload.revision, identity.user.username)


@router.post("/documents/{item_id}/withdraw")
async def withdraw_document(item_id: str, payload: VersionAction, identity: AdminIdentity = Depends(administrator),
                            db: AsyncSession = Depends(get_db)):
    """
    按版本撤回当前发布修订，并保留原文历史。
    """
    return await documents.withdraw_document(db, item_id, payload.expected_version, identity.user.username)


@router.get("/jobs")
async def job_list(page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                   db: AsyncSession = Depends(get_db)):
    """
    管理员查看真实的排队、运行、终态及受控错误摘要。
    """
    return await jobs.list_jobs(db, page, size)


@router.post("/jobs/index", status_code=202)
async def request_index(payload: KnowledgePayload, identity: AdminIdentity = Depends(administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    手动请求重建索引，相同活动任务合并；服务关闭时只排队不执行。
    """
    job = await jobs.enqueue_index(db, identity.user.username, dirty=False)
    record(db, identity.user.username, "knowledge.index.request", job.id)
    result = jobs.job_data(job)
    await catalog.commit(db)
    return result


@router.post("/jobs/{item_id}/cancel")
async def cancel_job(item_id: str, payload: VersionAction, identity: AdminIdentity = Depends(administrator),
                     db: AsyncSession = Depends(get_db)):
    """
    等待中的任务立即取消；运行中显示取消请求，执行器确认后进入终态。
    """
    result = await jobs.cancel_job(db, item_id, payload.expected_version, identity.user.username)
    await catalog.commit(db)
    return result


def import_directory():
    """
    延迟校验私有卷，未使用导入能力的既有后台不受配置缺失影响。
    """
    try:
        return import_files.import_directory(indexes.private_directory(settings.KNOWLEDGE_DATA_DIR))
    except (ValueError, OSError):
        raise HTTPException(503, "中心知识私有存储尚未就绪") from None


@router.post("/imports/upload", status_code=201)
async def upload_import(request: Request, source_id: str = Query(pattern=r"^[a-f0-9]{32}$"),
                        source_version: int = Query(ge=1), identity: AdminIdentity = Depends(administrator),
                        db: AsyncSession = Depends(get_db)):
    """
    鉴权和 CSRF 检查后流式接收单个未压缩 JSONL，不接受文件系统路径。
    """
    return await import_catalog.upload(db, import_directory(), request, source_id, source_version, identity.user.username)


@router.get("/imports")
async def import_list(page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                      db: AsyncSession = Depends(get_db)):
    """
    分页查看批次与受控摘要，不返回候选正文或原文件。
    """
    return await import_catalog.list_batches(db, page, size)


@router.get("/imports/{item_id}")
async def read_import(item_id: str, db: AsyncSession = Depends(get_db)):
    """
    展示批次和真实执行任务，已过期批次仍可查看状态元数据。
    """
    row = await catalog.get_row(db, KnowledgeImport, item_id)
    job = await db.get(KnowledgeJob, row.current_job_id) if row.current_job_id else None
    return {**import_catalog.batch_data(row), "job": jobs.job_data(job) if job else None,
            "configuration": json.loads(row.mapping)}


@router.post("/imports/{item_id}/preflight", status_code=202)
async def preflight_import(item_id: str, payload: ImportPreflight, identity: AdminIdentity = Depends(administrator),
                           db: AsyncSession = Depends(get_db)):
    """
    以明确字段、来源选择和适用范围排队预检，不执行导入或发布。
    """
    return await import_catalog.queue(db, import_directory(), item_id, payload, identity.user.username, phase="preflight")


@router.get("/imports/{item_id}/rows")
async def import_rows(item_id: str, page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                      db: AsyncSession = Depends(get_db)):
    """
    分页查看预检动作与警告，成功导入后改读无正文回执。
    """
    return await import_catalog.rows(db, import_directory(), item_id, page, size)


@router.get("/imports/{item_id}/rows/{line}")
async def import_row(item_id: str, line: int, db: AsyncSession = Depends(get_db)):
    """
    明确查看一行旧版与候选差异；秘密或其他阻止行不回显候选正文。
    """
    return await import_catalog.rows(db, import_directory(), item_id, 1, 1, line)


@router.post("/imports/{item_id}/apply", status_code=202)
async def apply_import(item_id: str, payload: ImportApproval, identity: AdminIdentity = Depends(administrator),
                       db: AsyncSession = Depends(get_db)):
    """
    显式批准所审阅计划与逐行决定，仅排队生成待审草稿。
    """
    return await import_catalog.queue(db, import_directory(), item_id, payload, identity.user.username, phase="apply")


@router.post("/imports/{item_id}/discard")
async def discard_import(item_id: str, payload: VersionAction, identity: AdminIdentity = Depends(administrator),
                         db: AsyncSession = Depends(get_db)):
    """
    丢弃非活动暂存；已提交的历史修订及行回执继续保留。
    """
    return await import_catalog.discard(db, import_directory(), item_id, payload.expected_version, identity.user.username)
