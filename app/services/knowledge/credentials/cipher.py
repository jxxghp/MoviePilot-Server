"""
私有部署密钥环和来源绑定的认证加密，不持久化解密后的请求头。
"""

import base64
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from app.core.config import settings


@dataclass(frozen=True)
class Keyring:
    """部署密钥只留在当前调用内存，调试表示不包含原始密钥。"""

    active: str
    keys: dict[str, bytes] = field(repr=False)


def keyring() -> Keyring:
    """
    每次读取私有密钥文件，轮换对所有 worker 后续操作生效，不从源码或默认值生成。
    """
    try:
        path = Path(settings.KNOWLEDGE_KEY_FILE)
        resolved = path.resolve()
        if (not settings.KNOWLEDGE_KEY_FILE or not path.is_absolute() or path.is_symlink()
                or resolved.is_relative_to(Path(__file__).resolve().parents[4])
                or resolved.is_relative_to("/private/tmp") or resolved.is_relative_to("/tmp")
                or any((parent / ".git").exists() for parent in (resolved, *resolved.parents))):
            raise ValueError("key_path")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or (hasattr(os, "getuid") and info.st_uid != os.getuid()) or info.st_size > 16384):
                raise ValueError("key_permissions")
            raw = stream.read(16385)
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {"active", "keys"}
                or not isinstance(data["keys"], dict) or not 1 <= len(data["keys"]) <= 8
                or data["active"] not in data["keys"]):
            raise ValueError("key_format")
        keys = {}
        for identity, encoded in data["keys"].items():
            if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", identity) or not isinstance(encoded, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43}=", encoded)):
                raise ValueError("key_value")
            value = base64.b64decode(encoded, altchars=b"-_", validate=True)
            if len(value) != 32:
                raise ValueError("key_size")
            keys[identity] = value
        return Keyring(data["active"], keys)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(503, "knowledge_keyring_unavailable") from None


def associated(source_id: str, binding: str, kind: str, version: int) -> bytes:
    """绑定密文的用途和稳定来源，防止数据库记录跨来源、类型或版本替换。"""
    return f"moviepilot-knowledge-credential:v1:{source_id}:{binding}:{kind}:{version}".encode()


def encrypt(source_id: str, binding: str, kind: str, version: int, value: str) -> dict:
    """每次使用新 nonce 和部署 active 密钥；调用方只接收待落库的密文元数据。"""
    ring = keyring()
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(ring.keys[ring.active]).encrypt(nonce, value.encode(),
                                                     associated(source_id, binding, kind, version))
    return {"key_id": ring.active, "nonce": base64.urlsafe_b64encode(nonce).decode(),
            "ciphertext": base64.urlsafe_b64encode(encrypted).decode()}


def decrypt(row) -> str:
    """只在出站或明确重加密时解密；缺少旧密钥或认证失败均固定拒绝。"""
    ring = keyring()
    try:
        nonce = base64.b64decode(row.nonce, altchars=b"-_", validate=True)
        encrypted = base64.b64decode(row.ciphertext, altchars=b"-_", validate=True)
        if len(nonce) != 12:
            raise ValueError("nonce_size")
        return AESGCM(ring.keys[row.key_id]).decrypt(
            nonce, encrypted, associated(row.source_id, row.binding, row.kind, row.version),
        ).decode()
    except (InvalidTag, ValueError, KeyError, TypeError):
        raise HTTPException(503, "knowledge_credential_unavailable") from None
