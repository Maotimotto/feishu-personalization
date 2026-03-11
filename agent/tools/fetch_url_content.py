"""Tool: 获取 URL 的标题和正文内容。"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from source_type import get_source_type, is_video_url

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_TITLE_RE = re.compile(
    r"""<meta\s[^>]*property\s*=\s*["']og:title["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_OG_TITLE_RE2 = re.compile(
    r"""<meta\s[^>]*content\s*=\s*["']([^"']+)["'][^>]*property\s*=\s*["']og:title["']""",
    re.IGNORECASE,
)

_GENERIC_TITLES = {
    "金十数据",
    "华尔街见闻",
    "华尔街见闻-实时行情新闻资讯一站全覆盖",
}

_JSONLD_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_P_TAG_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WX_CONTENT_RE = re.compile(
    r'id="js_content"[^>]*>(.*?)(?:</div>\s*<script|<div class="rich_media_area_extra")',
    re.DOTALL,
)
_OG_DESC_RE = re.compile(
    r"""<meta\s[^>]*property\s*=\s*["']og:description["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_OG_DESC_RE2 = re.compile(
    r"""<meta\s[^>]*content\s*=\s*["']([^"']+)["'][^>]*property\s*=\s*["']og:description["']""",
    re.IGNORECASE,
)
_META_DESC_RE = re.compile(
    r"""<meta\s[^>]*name\s*=\s*["']description["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_DOUYIN_URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com)/"
)

_UA_HEADER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _clean_title(raw: str) -> str:
    import html
    return html.unescape(raw).strip()


def _clean_content(text: str) -> str:
    import html as html_mod
    text = html_mod.unescape(text)
    text = text.replace("\xa0", " ").replace("&nbsp;", " ")
    lines = [line.strip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
        else:
            cleaned.append(line)
    text = "\n".join(cleaned).strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _extract_jsonld_title(html_text: str) -> str:
    for m in _JSONLD_RE.finditer(html_text):
        try:
            data = json.loads(m.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("headline", "name"):
                    val = item.get(key, "")
                    if val and val not in _GENERIC_TITLES:
                        return val.strip()
        except (json.JSONDecodeError, TypeError):
            continue
    return ""


def _is_weixin_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.lower() in ("mp.weixin.qq.com",)


def _extract_weixin_content(html_text: str) -> str:
    m = _WX_CONTENT_RE.search(html_text)
    if not m:
        return ""
    content_area = m.group(1)
    content_area = _BR_TAG_RE.sub("\n", content_area)
    text = _STRIP_TAGS_RE.sub("", content_area)
    return text


# ---------------------------------------------------------------------------
# Site-specific extractors
# ---------------------------------------------------------------------------


def _fetch_wallstreetcn(url: str) -> tuple[str, str] | None:
    m = re.search(r"wallstreetcn\.com/articles/(\d+)", url)
    if not m:
        return None
    article_id = m.group(1)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"https://api-one-wscn.awtmt.com/apiv1/content/articles/{article_id}?extract=1",
                headers=_UA_HEADER,
            )
            resp.raise_for_status()
        data = resp.json().get("data", {})
        title = data.get("title", "")
        content = data.get("content_short", "")
        if title:
            return title, content
    except Exception:
        pass
    return None


def _fetch_jin10(url: str) -> tuple[str, str] | None:
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(url, headers=_UA_HEADER)
            resp.raise_for_status()
            final_url = str(resp.url)
            m = re.search(r"[?&]id=(\d+)", final_url)
            if not m:
                m = re.search(r"/details/(\d+)", final_url)
            if not m:
                return None
            article_id = m.group(1)
            ssr_resp = client.get(
                f"https://xnews.jin10.com/details/{article_id}",
                headers=_UA_HEADER,
            )
            ssr_resp.raise_for_status()
        html = ssr_resp.text[:30_000]
        tm = _TITLE_TAG_RE.search(html)
        if tm:
            title = _clean_title(tm.group(1))
            title = re.sub(r"[-\|｜].*?金十.*$", "", title).strip()
            if title:
                return title, ""
    except Exception:
        pass
    return None


# Futunn sitemap cache
_futunn_sitemap_cache: dict[str, str] = {}
_futunn_sitemap_ts: float = 0.0
_FUTUNN_SITEMAP_TTL = 300


def _futunn_refresh_sitemap() -> None:
    import time as _time
    global _futunn_sitemap_ts
    _futunn_sitemap_cache.clear()

    sitemap_urls = [
        "https://news.futunn.com/sitemap-news-zhhans-index-test-quality-48hours.xml",
        "https://news.futunn.com/sitemap-news-zhhans-index-test-48hours.xml",
    ]
    for sitemap_url in sitemap_urls:
        try:
            resp = httpx.get(sitemap_url, headers=_UA_HEADER, timeout=15)
            resp.raise_for_status()
            entries = re.findall(
                r"<loc>https://news\.futunn\.com/post/(\d+)/[^<]*</loc>"
                r"\s*<news:news>.*?<news:title>(.*?)</news:title>",
                resp.text,
                re.DOTALL,
            )
            for pid, title in entries:
                if pid not in _futunn_sitemap_cache:
                    _futunn_sitemap_cache[pid] = title
        except Exception:
            continue
    _futunn_sitemap_ts = _time.time()


def _fetch_futunn(url: str) -> tuple[str, str] | None:
    import time as _time
    m = re.search(r"futunn\.com/post/(\d+)", url)
    if not m:
        return None
    post_id = m.group(1)

    now = _time.time()
    if not _futunn_sitemap_cache or now - _futunn_sitemap_ts > _FUTUNN_SITEMAP_TTL:
        _futunn_refresh_sitemap()

    sitemap_title = _futunn_sitemap_cache.get(post_id, "")
    if sitemap_title:
        return sitemap_title, ""

    try:
        with httpx.Client(follow_redirects=False, timeout=10) as client:
            resp = client.get(
                f"https://news.futunn.com/share/post/{post_id}",
                headers=_UA_HEADER,
            )
        location = resp.headers.get("location", "")
        slug_m = re.search(r"/post/\d+/(.+?)(?:\?|$)", location)
        if slug_m:
            slug = slug_m.group(1)
            title = slug.replace("-", " ").strip().title()
            if title:
                return title, ""
    except Exception:
        pass
    return None


_SITE_FETCHERS: list[tuple[str, object]] = [
    ("wallstreetcn.com", _fetch_wallstreetcn),
    ("jin10.com", _fetch_jin10),
    ("futunn.com", _fetch_futunn),
]


def _fetch_title_and_content(url: str) -> tuple[str, str]:
    """Fetch title and content for a single URL."""
    for domain, fetcher in _SITE_FETCHERS:
        if domain in url:
            result = fetcher(url)
            if result:
                return result

    skip_content = is_video_url(url)

    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(url, headers=_UA_HEADER)
            resp.raise_for_status()
    except Exception:
        return "", ""

    html_text = resp.text[:200_000]
    is_wx = _is_weixin_url(url)
    html_full = resp.text if is_wx else html_text

    # Title
    title = ""
    for pattern in (_OG_TITLE_RE, _OG_TITLE_RE2):
        m = pattern.search(html_text)
        if m:
            title = _clean_title(m.group(1))
            break
    if not title:
        m = _TITLE_TAG_RE.search(html_text)
        if m:
            title = _clean_title(m.group(1))
    if not title or title in _GENERIC_TITLES:
        jsonld_title = _extract_jsonld_title(html_text)
        if jsonld_title:
            title = jsonld_title

    # Content
    content = ""
    if not skip_content:
        if is_wx:
            content = _extract_weixin_content(html_full)
        else:
            paragraphs = []
            for m in _P_TAG_RE.finditer(html_text):
                text = _STRIP_TAGS_RE.sub("", m.group(1)).strip()
                if len(text) > 20:
                    paragraphs.append(text)
            if paragraphs:
                content = "\n".join(paragraphs)

        if not content:
            for pat in (_OG_DESC_RE, _OG_DESC_RE2, _META_DESC_RE):
                m = pat.search(html_text)
                if m:
                    content = _clean_title(m.group(1))
                    break

        if content:
            content = _clean_content(content)

    return title, content


# ---------------------------------------------------------------------------
# Douyin share text parsing
# ---------------------------------------------------------------------------


def _is_douyin_url(url: str) -> bool:
    return bool(_DOUYIN_URL_RE.match(url))


_DOUYIN_PASSCODE_RE = re.compile(
    r"[A-Za-z0-9]{1,5}@[A-Za-z0-9]{1,5}\.[A-Za-z0-9]{1,5}"
)


def _is_douyin_share_text(text: str) -> bool:
    # Standard passcode format: 4.52 f@o.DH https://...
    if re.match(r"\d+\.\d+\s+", text) and re.search(r"\S+:/", text):
        return True
    if "复制打开抖音" in text or "打开Dou音" in text:
        return True
    # Date prefix format: 01/06 f@o.DH 华尔街... or 04/29 AI圈...
    if re.match(r"\d{1,2}/\d{1,2}\s+", text):
        return True
    # Passcode only: Y@m.Qx 美国为什么...
    if _DOUYIN_PASSCODE_RE.match(text):
        return True
    return False


def _strip_douyin_noise(text: str) -> str:
    """Remove date prefixes and share passcodes from Douyin text."""
    # Remove leading date: 01/06, 09/11, 04/29 etc.
    text = re.sub(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*", "", text)
    # Remove Douyin share passcode: f@o.DH, t@R.xS, Y@m.Qx
    text = _DOUYIN_PASSCODE_RE.sub("", text)
    return text.strip()


def _extract_douyin_title(text: str) -> str:
    if not text or not _is_douyin_share_text(text):
        return ""
    text = re.sub(
        r"\s*https?://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com)/\S*.*",
        "", text,
    )
    text = re.sub(r"\s+-\s+抖音.*", "", text)
    text = re.sub(r"\s*#\s*\S+", "", text)
    # Strip date and passcode noise
    text = _strip_douyin_noise(text)
    bracket_match = re.search(r"【[^】]*】\s*", text)
    if bracket_match:
        text = text[bracket_match.end():]
    else:
        passcode_match = re.match(r".*?\S+:/\s*", text)
        if passcode_match:
            text = text[passcode_match.end():]
    text = text.strip()
    ellipsis_match = re.match(r"(.+?(?:\.\.\.|…))\s+.+", text)
    if ellipsis_match:
        text = ellipsis_match.group(1)
    return text.strip()


def _get_message_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, dict):
        return content.get("text", "")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed.get("text", "") if isinstance(parsed, dict) else content
        except (json.JSONDecodeError, TypeError):
            return content
    return ""


def _find_message_text_for_url(messages: list[dict], url: str) -> str:
    for msg in messages:
        text = _get_message_text(msg)
        if url in text:
            return text
    return ""


@tool
def fetch_url_content(urls_json: str) -> str:
    """获取 URL 列表中每个链接的标题和正文内容。

    支持 60+ 来源的智能提取，包括华尔街见闻（API）、金十数据（SSR）、
    富途牛牛（Sitemap）、微信公众号（DOM）、抖音（分享文本解析）等。
    自动识别来源类型，对视频链接跳过内容抓取仅提取标题。

    ## 何时使用
    - 提取到链接后，需要获取每个链接的标题用于热词分类时
    - 这是链接汇总流程的第三步（extract_urls → fetch_url_content）
    - search_web 返回的结果中某条链接需要获取完整内容时，也可以使用

    ## 何时不用
    - 只需要 URL 列表不需要标题/内容时
    - 链接已通过卡片消息获得了足够的标题信息且不需要正文时

    ## 输入来源
    直接接收 extract_urls 的完整 JSON 输出（包含 urls 和 messages 字段）。
    也可接收手动构造的 URL 列表 JSON。

    ## 输出去向
    输出的 JSON 传给 classify_titles 进行热词分类。
    注意：classify_titles 只使用 titles，但 create_feishu_spreadsheet 还需要
    本工具输出的 results 字段（含 url、source_type、content），需要在分类后合并数据。

    ## 注意
    - 部分链接可能抓取失败（返回空标题），agent 应在结果中检查并告知用户
    - 视频链接（抖音、B站等）只提取标题不抓取正文
    - 抖音链接通过解析分享文本中的标题，需要 messages 字段提供原始消息上下文

    Args:
        urls_json: extract_urls 的完整 JSON 输出，或包含 urls 列表的 JSON。

    Returns:
        JSON 字符串，包含 url_count 和 results 列表（每项含 url、title、source_type、content）。
    """
    data = json.loads(urls_json)
    url_items = data.get("urls", data) if isinstance(data, dict) else data
    messages = data.get("messages", []) if isinstance(data, dict) else []

    results: list[dict] = []
    _wx_fetched = False  # Track whether we've already fetched a weixin URL
    for item in url_items:
        url = item.get("url", item) if isinstance(item, dict) else str(item)
        existing_title = item.get("title", "") if isinstance(item, dict) else ""
        src_type = get_source_type(url)

        # Rate-limit weixin URLs: 5s delay between consecutive fetches
        is_wx = _is_weixin_url(url)
        if is_wx and _wx_fetched:
            print(f"[fetch_url_content] 微信公众号限速：等待5秒后抓取 {url[:80]}")
            time.sleep(5)

        if _is_douyin_url(url):
            msg_text = _find_message_text_for_url(messages, url)
            title = _extract_douyin_title(msg_text) or _strip_douyin_noise(existing_title) or existing_title
            content = ""
        elif existing_title and is_video_url(url):
            title = existing_title
            content = ""
        else:
            title, content = _fetch_title_and_content(url)
            if not title and existing_title:
                title = existing_title

        if is_wx:
            _wx_fetched = True

        results.append({
            "url": url,
            "title": title,
            "source_type": src_type,
            "content": content,
        })

    output = {
        "url_count": len(results),
        "results": results,
    }
    return json.dumps(output, ensure_ascii=False)
