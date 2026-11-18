"""
来源独占凭据的受控维护与出站取用，版本与目标范围共同保护并发更新。
"""

from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import delete, select

from app.admin.audit import record
from app.models.knowledge import KnowledgeCredential, KnowledgeSource
from app.services.knowledge import jobs
from app.services.knowledge.catalog import commit, get_row, replace_values
from app.services.knowledge.credentials import cipher
from app.services.knowledge.network.client import AuthMaterial
from app.services.knowledge.network.policy import NetworkError, binding, source_scope


async def credential(db, source_id):
    """按来源精确查找，不接受调用方指定共享秘密 ID。"""
    return await db.scalar(select(KnowledgeCredential).where(KnowledgeCredential.source_id == source_id))


def metadata(source, row) -> dict:
    """仅返回配置状态；configured 不保证上游账号仍有效，不解密或返回秘密。"""
    state = "missing"
    if row is not None:
        state = "configured"
        try:
            if row.binding != binding(source) or row.key_id not in cipher.keyring().keys:
                state = "unavailable"
        except (HTTPException, ValueError, TypeError):
            state = "unavailable"
    return {"configured": row is not None, "state": state, "source_version": source.version,
            "version": row.version if row else 0, "kind": row.kind if row else None, "host": row.host if row else None}


async def read(db, source_id) -> dict:
    """普通管理员可查看受控状态，以便理解为什么不能更改绑定的目标配置。"""
    source = await get_row(db, KnowledgeSource, source_id)
    return metadata(source, await credential(db, source_id))


async def locked(db, source_id, payload):
    """统一状态锁顺序，并在任何写入前核对来源和凭据两个并发版本。"""
    await jobs.lock_state(db)
    source = await get_row(db, KnowledgeSource, source_id, for_update=True)
    row = await credential(db, source_id)
    if source.version != payload.expected_source_version or (row.version if row else 0) != payload.expected_credential_version:
        raise HTTPException(409, "knowledge_credential_changed")
    return source, row


async def save(db, source_id, payload, actor) -> dict:
    """明确替换认证值；密文、来源版本和不含秘密的审计一次提交。"""
    source, row = await locked(db, source_id, payload)
    try:
        scope = source_scope(source)
    except NetworkError:
        raise HTTPException(422, "knowledge_credential_source_invalid") from None
    if source.connector == "github" and payload.kind != "bearer":
        raise HTTPException(422, "knowledge_github_requires_bearer")
    version = row.version + 1 if row else 1
    encrypted = cipher.encrypt(source_id, scope.binding, payload.kind, version, payload.secret.get_secret_value())
    values = {**encrypted, "kind": payload.kind, "host": scope.host, "binding": scope.binding}
    if row is None:
        row = KnowledgeCredential(source_id=source_id, version=1, **values)
        db.add(row)
    else:
        await replace_values(db, row, row.version, values)
    await replace_values(db, source, payload.expected_source_version, {})
    record(db, actor, "knowledge.credential.replace", source_id, {"kind": payload.kind, "version": version})
    await commit(db)
    await db.refresh(source)
    await db.refresh(row)
    return metadata(source, row)


async def clear(db, source_id, payload, actor) -> dict:
    """清除当前密文；没有配置时仅在双方版本仍一致的条件下作为空操作返回。"""
    source, row = await locked(db, source_id, payload)
    if row is not None:
        await db.execute(delete(KnowledgeCredential).where(KnowledgeCredential.id == row.id))
        await replace_values(db, source, payload.expected_source_version, {})
        record(db, actor, "knowledge.credential.clear", source_id)
        await commit(db)
        await db.refresh(source)
    return metadata(source, None)


async def rewrap(db, source_id, payload, actor) -> dict:
    """保留认证值，用部署 active 密钥重新加密；旧密钥缺失时不能猜测或覆盖。"""
    source, row = await locked(db, source_id, payload)
    if row is None:
        raise HTTPException(409, "knowledge_credential_missing")
    if row.binding != binding(source):
        raise HTTPException(409, "knowledge_credential_scope_changed")
    value = cipher.decrypt(row)
    encrypted = cipher.encrypt(source_id, row.binding, row.kind, row.version + 1, value)
    await replace_values(db, row, row.version, encrypted)
    await replace_values(db, source, payload.expected_source_version, {})
    record(db, actor, "knowledge.credential.rewrap", source_id, {"version": payload.expected_credential_version + 1})
    await commit(db)
    await db.refresh(source)
    await db.refresh(row)
    return metadata(source, row)


async def material(db, source) -> AuthMaterial | None:
    """仅供中心执行器取用当前来源的内存认证材料，不形成可调用回读接口。"""
    row = await credential(db, source.id)
    if row is None:
        return None
    if row.binding != binding(source):
        raise HTTPException(503, "knowledge_credential_scope_changed")
    return AuthMaterial(source.id, row.binding, row.kind, SecretStr(cipher.decrypt(row)))
