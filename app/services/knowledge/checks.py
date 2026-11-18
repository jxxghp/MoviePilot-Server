"""
导入与后续采集共用的有限启发式检查；只返回固定标记，不声称可检测所有秘密。
"""

import re

PLACEHOLDERS = {"none", "null", "example", "changeme", "your_token", "your_passkey", "your_api_key"}


def content_flags(text: str) -> set[str]:
    """
    标记疑似凭据与内容指令；知识文本始终作为数据处理，不执行其中的要求。
    """
    result = set()
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,})", text):
        result.add("suspected_secret")
    assignments = re.finditer(
        r"""(?ix)["']?(?:cookie|passkey|token|password|api_key|authorization)["']?\s*[:=]\s*
            (?P<quote>["']?)(?P<value>[^"'\r\n,\x60]{16,})(?P=quote)""",
        text,
    )
    for match in assignments:
        value = match.group("value").strip().lower()
        if not match.group("quote") and (
            "(" in value or value.startswith(("self.", "cls.", "config.", "request.", "response."))
        ):
            continue
        if value not in PLACEHOLDERS and not value.startswith((chr(36) + "{", "<", "settings.", "os.environ", "your_")):
            result.add("suspected_secret")
    if re.search(r"(?i)[?&](?:passkey|token|api_key|password)=[A-Za-z0-9_%+-]{8,}", text):
        result.add("suspected_secret")
    if re.search(r"(?i)ignore.{0,40}(?:previous|system).{0,20}instructions|"
                 r"忽略.{0,20}(?:指令|提示词)|泄露.{0,20}(?:密钥|令牌|提示词)|你现在是", text):
        result.add("content_instruction")
    return result
