# scrapers/wechat/official_article_fetcher.py
"""公众号正文 web 直取：mp.weixin.qq.com/s/xxx -> {title, author, description, body}。

设计要点（已实测验证）：
- 公众号文章 HTML 是**服务端渲染**的，正文在 `<div id="js_content">`，直接
  HTTP GET 即可拿到全文。**无需微信客户端、无需 cookie、无需元宝、无需特殊 UA**
  （普通桌面 Chrome UA 即可，MicroMessenger UA 不增不减效果）。
  对比 official_pc.py 的 `_extract_post_content`（OCR 取前 50 行+过滤，慢且丢内容），
  本方法 ~1 秒/篇，拿到完整干净正文。
- 元数据从页面 JS 变量抽：`var msg_title` / `var msg_desc` / `var nickname` /
  `var user_name`，og:title/og:description 作兜底。
- **只支持短链 `mp.weixin.qq.com/s/xxx`**（用户 Excel 即此形式）。长链
  `s?__biz=...&sn=...&poc_token=...` 的 poc_token 是微信反爬临时令牌，有时效，
  失效后返回"环境异常"验证页（js_content 空）。短链不触发此问题。
- 与评论(OCR)解耦：本模块只做"链接->正文"，不碰评论，不依赖微信客户端。
  调用方拿到 None 时回退到 OCR 路径。
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 模块级 Session：cookie 跨文章持久化，提高拿到全文页（而非验证页）的概率
_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)

# js_content 缺失或正文过短 => 命中验证页/异常页（meta_description 兜底要求 >=20）
_MIN_BODY_LEN = 20


def is_official_article_url(url: str) -> bool:
    """是否为公众号文章链接（mp.weixin.qq.com/s 或 s?__biz）。"""
    return bool(url) and "mp.weixin.qq.com/s" in url


def _js_var(html: str, name: str) -> str:
    """抽 `var <name> = <value>;`，剥掉 htmlDecode(...)/.html(false)/引号。"""
    m = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*(.+?);", html, re.S)
    if not m:
        return ""
    val = m.group(1).strip()
    # htmlDecode("...")  或  htmlDecode('...')
    m2 = re.match(r'htmlDecode\(\s*(["\'])(.*?)\1\s*\)', val, re.S)
    if m2:
        return m2.group(2)
    # '...'.html(false)  或  "..."
    m2 = re.match(r'(["\'])(.*?)\1(?:\.html\([^)]*\))?', val, re.S)
    if m2:
        return m2.group(2)
    return val


def _og_meta(html: str, prop: str) -> str:
    m = re.search(
        r'"og:' + prop + r'"\s+content="([^"]*)"', html
    ) or re.search(
        r'name="og:' + prop + r'"\s+content="([^"]*)"', html
    )
    return m.group(1) if m else ""


def _extract_body(html: str) -> str:
    """抽正文：优先 <div id="js_content">；反爬变体页改从 <meta name="description">
    取（正文被塞进 meta、用 JS \\xNN 转义：\\x0a→换行 \\x26→&）。命中任一即返回纯文本。
    """
    # 1) js_content div（正常页）
    m = re.search(
        r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*<!--', html, re.S
    ) or re.search(r'<div[^>]*id="js_content"[^>]*>(.*?)</div>', html, re.S)
    if m:
        raw = m.group(1)
        # <p>/<br>/<div> 边界转成换行，其余 tag 剥掉
        text = re.sub(r"<(?:p|br|/p|div|/div)[^>]*>", "\n", raw, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = (
            text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        text = re.sub(r"\n{2,}", "\n", text).strip()
        if len(text) >= _MIN_BODY_LEN:
            return text

    # 2) <meta name="description">（反爬变体页正文塞这里）
    mm = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.S) or \
        re.search(r'<meta\s+content="([^"]*)"\s+name="description"', html, re.S)
    if mm:
        raw = mm.group(1)
        # 解码 JS \xNN 转义（\x0a→换行 \x26→& \x27→' \x22→"）
        decoded = re.sub(r"\\x([0-9a-fA-F]{2})", lambda x: chr(int(x.group(1), 16)), raw)
        decoded = (
            decoded.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        text = re.sub(r"<[^>]+>", "", decoded)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text).strip()
        if len(text) >= _MIN_BODY_LEN:
            return text
    return ""


def fetch_article_body(url: str, timeout: int = 20) -> dict | None:
    """直连 HTTP 取公众号正文 + 元数据。

    返回 dict: {title, author, description, gh_id, body, html_size}。
    命中验证页/异常/无正文返回 None（调用方可回退 OCR）。
    """
    if not is_official_article_url(url):
        logger.warning(f"[official_fetch] 非公众号链接: {url[:60]}")
        return None
    try:
        r = _SESSION.get(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[official_fetch] 请求失败 {url[:60]}: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"[official_fetch] HTTP {r.status_code}: {url[:60]}")
        return None
    html = r.text
    body = _extract_body(html)
    if len(body) < _MIN_BODY_LEN:
        logger.warning(
            f"[official_fetch] 正文过短({len(body)}字)，疑似验证页: {url[:60]}"
        )
        return None
    result = {
        "title": _js_var(html, "msg_title") or _og_meta(html, "title"),
        "author": _js_var(html, "nickname"),
        "description": _js_var(html, "msg_desc") or _og_meta(html, "description"),
        "gh_id": _js_var(html, "user_name"),
        "body": body,
        "html_size": len(html),
    }
    logger.info(
        f"[official_fetch] 正文 {len(body)}字, title={result['title'][:24]!r}, "
        f"author={result['author'][:16]!r}"
    )
    return result
