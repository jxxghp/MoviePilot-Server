"""分享删除令牌的生成、散列与校验。"""

import hashlib
import hmac
import secrets


def create_share_owner_token() -> str:
    """生成仅返回给分享创建者一次的高熵删除令牌。"""
    return secrets.token_urlsafe(32)


def hash_share_owner_token(token: str) -> str:
    """计算数据库保存的令牌摘要，避免持久化明文凭据。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_share_owner_token(token: str | None, token_hash: str | None) -> bool:
    """使用常量时间比较校验删除令牌。"""
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_share_owner_token(token), token_hash)
