"""
有限文档解码、HTML 正文提取和秘密检查；不执行脚本或把登录页当作知识。
"""

import codecs
import re
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup, UnicodeDammit
from markdownify import markdownify
from soupsieve.util import SelectorSyntaxError

from app.schemas.knowledge import public_url
from app.services.knowledge.checks import content_flags
from app.services.knowledge.network.policy import NetworkError

MAX_CHARACTERS = 262144
MAX_NODES = 30000


def decode(body, charset=None):
    """保留明确声明字符集，未知字符集或损坏字节拒绝，不用替换字符污染中文知识。"""
    try:
        encoding = charset or "utf-8-sig"
        codecs.lookup(encoding)
        return body.decode(encoding)
    except (LookupError, UnicodeError):
        raise NetworkError("document_encoding_invalid") from None


def check_text(text, auth=None):
    """秘密或认证反射阻止写入；内容指令仅标记警告，永远不执行。"""
    if len(text) > MAX_CHARACTERS:
        raise NetworkError("document_too_large")
    if len(text.strip()) < 20 or "\x00" in text:
        raise NetworkError("document_empty")
    flags = content_flags(text)
    if auth is not None:
        raw = auth.value.get_secret_value()
        values = [raw] if auth.kind == "bearer" else [part.partition("=")[2].strip() for part in raw.split(";")]
        if (raw in text or quote(raw, safe="") in text
                or any(value and len(value) >= 8 and (value in text or quote(value, safe="") in text) for value in values)):
            flags.add("suspected_secret")
    if "suspected_secret" in flags:
        raise NetworkError("suspected_secret")
    return sorted(flags)


def html_document(result, source):
    """按显式选择器或固定正文模板提取 HTML，删除表单、脚本、导航和不安全链接。"""
    if result.body.count(b"<") > MAX_NODES:
        raise NetworkError("document_structure_large")
    try:
        if result.charset:
            raw = decode(result.body, result.charset)
        else:
            raw = UnicodeDammit(result.body, is_html=True).unicode_markup
            if raw is None or "\ufffd" in raw:
                raise NetworkError("document_encoding_invalid")
        soup = BeautifulSoup(raw, "html.parser")
        if soup.select_one("#challenge-form,#cf-challenge-running"):
            raise NetworkError("authentication_page")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if re.fullmatch(r"(?i)\s*(?:(?:登录|登入|用户登录|login|sign in)(?:\s*[-|—].*)?"
                        r"|just a moment.*|attention required.*|验证码|人机验证)\s*", title):
            raise NetworkError("authentication_page")
        if source.selector:
            roots = soup.select(source.selector)
        elif source.connector == "nexusphp":
            roots = soup.select("#main, .mainouter, td.embedded, main, article")
        else:
            roots = soup.select("main, article, .markdown-body, .contents, #content, .content")
        if not roots:
            raise NetworkError("document_selector_empty")
        identities = {id(root) for root in roots}
        roots = [root for root in roots if not any(id(parent) in identities for parent in root.parents)]
        # NexusPHP FAQ 的外层正文已包含大量嵌套单元格；去掉重复子树后再限制独立正文数量。
        if len(roots) > 50:
            raise NetworkError("document_structure_large")
        selected = BeautifulSoup("".join(str(root) for root in roots), "html.parser")
        if selected.select_one('input[type="password"],form[action*="login"]'):
            raise NetworkError("authentication_page")
        nodes = selected.find_all(True)
        if len(nodes) > MAX_NODES:
            raise NetworkError("document_structure_large")
        if source.connector == "nexusphp":
            for node in selected.select("#nav_block,#info_block,#userinfo,#user_info,#userinfobar"):
                node.decompose()
        for node in selected.select("script,style,form,input,button,iframe,nav,footer,noscript,svg,img,video,audio"):
            node.decompose()
        for node in selected.select("header"):
            if node.find_parent(["article", "main"]) is None:
                node.decompose()
        if source.connector == "nexusphp":
            # mainouter 是页面布局；只拆其直属行列，内部真正的规则数据表保留。
            for table in selected.select("table.mainouter"):
                for node in table.find_all(["thead", "tbody", "tfoot", "tr", "td", "th"]):
                    if node.find_parent("table") is table:
                        node.unwrap()
                table.unwrap()
        for node in selected.find_all("a"):
            try:
                node["href"] = public_url(urljoin(result.url, str(node.get("href", ""))), allow_fragment=True)
                node.attrs = {"href": node["href"]}
            except ValueError:
                node.unwrap()
        heading = selected.find(["h1", "h2"])
        title = (heading.get_text(" ", strip=True) if heading else title) or result.url
        content = markdownify(str(selected), heading_style="ATX", bullets="-", autolinks=False).strip()
        return title[:300], content
    except (SelectorSyntaxError, ValueError, TypeError, RecursionError):
        raise NetworkError("document_parse_failed") from None


def document(result, source, title, *, auth=None):
    """已知格式才解码，不把二进制或未知 304 当作完整本地知识。"""
    if result.status == 304:
        raise NetworkError("not_modified_without_baseline")
    if result.content_type in {"text/html", "application/xhtml+xml"}:
        title, text = html_document(result, source)
    elif result.content_type in {"text/plain", "text/markdown"}:
        text = decode(result.body, result.charset).strip()
        if re.match(r"(?i)^(?:请先登录|未登录|会话已过期|登录失败|access denied|authentication required|please (?:log|sign) in)", text):
            raise NetworkError("authentication_page")
    else:
        raise NetworkError("document_type_unsupported")
    flags = check_text(title + "\n" + text, auth)
    if len(text.strip()) < 20:
        raise NetworkError("document_empty")
    return {"title": title[:300], "content": text, "flags": flags}
