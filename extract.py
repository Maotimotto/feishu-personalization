"""Extract URLs from chat messages and fetch their page titles + content."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

from source_type import get_source_type, is_video_url

# Domains to skip — internal admin/platform URLs, not real content
_SKIP_DOMAINS = {"open.feishu.cn", "open.larksuite.com"}


def _should_skip_url(url: str) -> bool:
    """Return True if the URL belongs to a domain we should ignore."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.lower() in _SKIP_DOMAINS

# Match http/https URLs — exclude CJK characters and fullwidth punctuation
_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")

# Markdown link: [text](url)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")

# <title>…</title> (lazy, case-insensitive)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# <meta property="og:title" content="…">
_OG_TITLE_RE = re.compile(
    r"""<meta\s[^>]*property\s*=\s*["']og:title["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_OG_TITLE_RE2 = re.compile(
    r"""<meta\s[^>]*content\s*=\s*["']([^"']+)["'][^>]*property\s*=\s*["']og:title["']""",
    re.IGNORECASE,
)

# Known brand-only titles from SPA sites that don't render real titles server-side
_GENERIC_TITLES = {
    "金十数据",
    "华尔街见闻",
    "华尔街见闻-实时行情新闻资讯一站全覆盖",
}

# JSON-LD structured data
_JSONLD_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _extract_jsonld_title(html_text: str) -> str:
    """Try to extract a headline/name from JSON-LD structured data."""
    for m in _JSONLD_RE.finditer(html_text):
        try:
            data = json.loads(m.group(1))
            # Could be a single object or a list
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


# Douyin share link domains
_DOUYIN_URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com)/"
)


def extract_urls(messages: list[dict]) -> list[str]:
    """Extract deduplicated URLs from text-type message content."""
    seen: set[str] = set()
    urls: list[str] = []
    for msg in messages:
        if msg.get("msg_type") not in (None, "text"):
            continue
        content = msg.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
                text = parsed.get("text", "") if isinstance(parsed, dict) else content
            except (json.JSONDecodeError, TypeError):
                text = content
        else:
            continue

        for match in _URL_RE.finditer(text):
            url = match.group().rstrip(",.;:!?)>")
            if not url or url in seen:
                continue
            if _should_skip_url(url):
                print(f"  [SKIP] {url} (noise domain)")
                continue
            seen.add(url)
            urls.append(url)
    return urls


def extract_from_cards(messages: list[dict]) -> list[dict]:
    """Extract {url, title} pairs from interactive card messages.

    Parses card header for title, and card elements for URLs
    (button actions, markdown links).
    """
    results: list[dict] = []
    seen: set[str] = set()

    for msg in messages:
        if msg.get("msg_type") != "interactive":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(content, dict):
            continue

        # Title from card header
        header = content.get("header") or {}
        title_obj = header.get("title") or {}
        card_title = title_obj.get("content", "") if isinstance(title_obj, dict) else ""

        # Collect URLs from card elements
        card_urls: list[str] = []
        for element in content.get("elements") or []:
            # Button actions with url field
            for action in element.get("actions") or []:
                url = action.get("url") or action.get("multi_url", {}).get("url")
                if url:
                    card_urls.append(url.rstrip(",.;:!?)>"))

            # Markdown content with [text](url) links
            md_content = element.get("content", "")
            if isinstance(md_content, str):
                for m in _MD_LINK_RE.finditer(md_content):
                    card_urls.append(m.group(2).rstrip(",.;:!?)>"))

            # Plain URLs in markdown content
            if isinstance(md_content, str):
                for m in _URL_RE.finditer(md_content):
                    url = m.group().rstrip(",.;:!?)>")
                    if url not in card_urls:
                        card_urls.append(url)

            # href in text elements (div > text)
            text_obj = element.get("text")
            if isinstance(text_obj, dict):
                href = text_obj.get("href")
                if href:
                    card_urls.append(href.rstrip(",.;:!?)>"))

        for url in card_urls:
            if not url or url in seen:
                continue
            if _should_skip_url(url):
                print(f"  [SKIP-CARD] {url} (noise domain)")
                continue
            seen.add(url)
            results.append({"url": url, "title": card_title})

    return results


def fetch_title(url: str) -> str:
    """Fetch a URL and extract the page title. Returns empty string on failure."""
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
            )
            resp.raise_for_status()
    except Exception:
        return ""

    html = resp.text[:30_000]  # only scan first 30KB

    # Prefer og:title
    for pattern in (_OG_TITLE_RE, _OG_TITLE_RE2):
        m = pattern.search(html)
        if m:
            return _clean_title(m.group(1))

    # Fall back to <title>
    m = _TITLE_TAG_RE.search(html)
    if m:
        return _clean_title(m.group(1))

    return ""


def _clean_title(raw: str) -> str:
    """Unescape HTML entities and strip whitespace."""
    import html

    return html.unescape(raw).strip()


# ---------------------------------------------------------------------------
# Content extraction (for non-video pages)
# ---------------------------------------------------------------------------

# Match <p>…</p> tags
_P_TAG_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
# Strip HTML tags
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
# <br> / <br /> tags → newline
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# WeChat article content area (id="js_content")
_WX_CONTENT_RE = re.compile(
    r'id="js_content"[^>]*>(.*?)(?:</div>\s*<script|<div class="rich_media_area_extra")',
    re.DOTALL,
)
# og:description
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


def _clean_content(text: str) -> str:
    """Clean extracted content: remove extra spaces, newlines, and non-breaking spaces."""
    import html as html_mod

    # Unescape HTML entities
    text = html_mod.unescape(text)
    # Replace non-breaking spaces with regular spaces
    text = text.replace("\xa0", " ").replace("&nbsp;", " ")
    # Normalize each line: strip leading/trailing whitespace
    lines = [line.strip() for line in text.splitlines()]
    # Remove blank lines, collapse consecutive blank lines
    cleaned: list[str] = []
    for line in lines:
        if not line:
            # Only add one blank line between paragraphs
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
        else:
            cleaned.append(line)
    text = "\n".join(cleaned).strip()
    # Collapse runs of multiple spaces into one
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _is_weixin_url(url: str) -> bool:
    """Check if URL is a WeChat public account article."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.lower() in ("mp.weixin.qq.com",)


def _extract_weixin_content(html_text: str) -> str:
    """Extract article content from WeChat public account HTML.

    WeChat articles use <section><span>text</span></section> inside
    the id="js_content" div, not <p> tags.
    """
    m = _WX_CONTENT_RE.search(html_text)
    if not m:
        return ""
    content_area = m.group(1)
    # Replace <br> tags with newlines
    content_area = _BR_TAG_RE.sub("\n", content_area)
    # Strip all HTML tags
    text = _STRIP_TAGS_RE.sub("", content_area)
    return text


# ---------------------------------------------------------------------------
# Site-specific extractors for SPA sites that don't render metadata server-side
# ---------------------------------------------------------------------------

_UA_HEADER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _fetch_wallstreetcn(url: str) -> tuple[str, str] | None:
    """Fetch title + content via wallstreetcn API."""
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
    """Fetch title from jin10 SSR route."""
    # go.jin10.com short links redirect to xnews.jin10.com/webapp/details.html?id=XXX
    # The SSR route xnews.jin10.com/details/XXX has proper <title>
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(url, headers=_UA_HEADER)
            resp.raise_for_status()
            final_url = str(resp.url)
            # Extract article ID from the final URL
            m = re.search(r"[?&]id=(\d+)", final_url)
            if not m:
                m = re.search(r"/details/(\d+)", final_url)
            if not m:
                return None
            article_id = m.group(1)
            # Fetch the SSR page
            ssr_resp = client.get(
                f"https://xnews.jin10.com/details/{article_id}",
                headers=_UA_HEADER,
            )
            ssr_resp.raise_for_status()
        html = ssr_resp.text[:30_000]
        tm = _TITLE_TAG_RE.search(html)
        if tm:
            title = _clean_title(tm.group(1))
            # Remove trailing site name suffix like "-市场参考-金十数据"
            title = re.sub(r"[-\|｜].*?金十.*$", "", title).strip()
            if title:
                return title, ""
    except Exception:
        pass
    return None


def _fetch_futunn(url: str) -> tuple[str, str] | None:
    """Fetch title from futunn via sitemap or /share/ redirect slug."""
    m = re.search(r"futunn\.com/post/(\d+)", url)
    if not m:
        return None
    post_id = m.group(1)

    # 1. Try sitemaps for Chinese title (covers articles from last 48h)
    sitemap_title = _futunn_sitemap_lookup(post_id)
    if sitemap_title:
        return sitemap_title, ""

    # 2. Fallback: get English slug from /share/ redirect
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


# Cache: {post_id: title} from the most recent sitemap fetch
_futunn_sitemap_cache: dict[str, str] = {}
_futunn_sitemap_ts: float = 0.0
_FUTUNN_SITEMAP_TTL = 300  # refresh every 5 min


def _futunn_sitemap_lookup(post_id: str) -> str:
    """Look up a futunn post title from the news sitemap (cached)."""
    import time as _time

    now = _time.time()
    if not _futunn_sitemap_cache or now - _futunn_sitemap_ts > _FUTUNN_SITEMAP_TTL:
        _futunn_refresh_sitemap()

    return _futunn_sitemap_cache.get(post_id, "")


def _futunn_refresh_sitemap() -> None:
    """Fetch futunn zh-hans sitemaps and populate the cache."""
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
    if _futunn_sitemap_cache:
        print(f"  [futunn] Cached {len(_futunn_sitemap_cache)} titles from sitemaps")


# Map domain substrings to their specific fetcher
_SITE_FETCHERS: list[tuple[str, object]] = [
    ("wallstreetcn.com", _fetch_wallstreetcn),
    ("jin10.com", _fetch_jin10),
    ("futunn.com", _fetch_futunn),
]


def fetch_title_and_content(url: str) -> tuple[str, str]:
    """Fetch a URL and extract both title and content.

    For video URLs, content is skipped.
    Returns (title, content) — empty strings on failure.
    """
    # Try site-specific extractors first (for SPA sites)
    for domain, fetcher in _SITE_FETCHERS:
        if domain in url:
            result = fetcher(url)
            if result:
                return result

    skip_content = is_video_url(url)

    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
            )
            resp.raise_for_status()
    except Exception:
        return "", ""

    html_text = resp.text[:200_000]

    # WeChat articles embed massive JS before the content area, so
    # we need to search the full HTML for the js_content div.
    is_wx = _is_weixin_url(url)
    html_full = resp.text if is_wx else html_text

    # --- Title ---
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

    # If title is a generic brand name (SPA site), try JSON-LD for a real title
    if not title or title in _GENERIC_TITLES:
        jsonld_title = _extract_jsonld_title(html_text)
        if jsonld_title:
            title = jsonld_title

    # --- Content (non-video only) ---
    content = ""
    if not skip_content:
        # WeChat articles: use js_content div extraction
        if is_wx:
            content = _extract_weixin_content(html_full)
        else:
            # General: extract all <p> tags to form the full article
            paragraphs = []
            for m in _P_TAG_RE.finditer(html_text):
                text = _STRIP_TAGS_RE.sub("", m.group(1)).strip()
                if len(text) > 20:
                    paragraphs.append(text)
            if paragraphs:
                content = "\n".join(paragraphs)

        # Fallback: use og:description / meta description if no content found
        if not content:
            for pat in (_OG_DESC_RE, _OG_DESC_RE2, _META_DESC_RE):
                m = pat.search(html_text)
                if m:
                    content = _clean_title(m.group(1))
                    break

        # Clean up whitespace and newlines for all non-video content
        if content:
            content = _clean_content(content)

    return title, content


# ---------------------------------------------------------------------------
# Douyin share text parsing
# ---------------------------------------------------------------------------


def _is_douyin_url(url: str) -> bool:
    """Check if a URL is a Douyin link."""
    return bool(_DOUYIN_URL_RE.match(url))


def _is_douyin_share_text(text: str) -> bool:
    """Check if text matches the pattern of a Douyin share text."""
    # Format A: starts with passcode like "3.33 02/03 xxx kPK:/"
    if re.match(r"\d+\.\d+\s+", text) and re.search(r"\S+:/", text):
        return True
    # Format B: contains "复制打开抖音" or "打开Dou音"
    if "复制打开抖音" in text or "打开Dou音" in text:
        return True
    return False


def _get_message_text(msg: dict) -> str:
    """Extract plain text from a message dict."""
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
    """Find the full message text containing the given URL."""
    for msg in messages:
        text = _get_message_text(msg)
        if url in text:
            return text
    return ""


def extract_douyin_title(text: str) -> str:
    """Extract video title from a Douyin share text.

    Returns empty string if the text is not a recognized share format
    or if extraction fails.
    """
    if not text or not _is_douyin_share_text(text):
        return ""

    # 1. Remove Douyin URL and everything after it
    text = re.sub(
        r"\s*https?://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com)/\S*.*",
        "",
        text,
    )

    # 2. Remove " - 抖音" suffix and everything after
    text = re.sub(r"\s+-\s+抖音.*", "", text)

    # 3. Remove hashtags (#tag or # tag)
    text = re.sub(r"\s*#\s*\S+", "", text)

    # 4. Remove prefix
    # Format B: everything up to and including 【author】
    bracket_match = re.search(r"【[^】]*】\s*", text)
    if bracket_match:
        text = text[bracket_match.end() :]
    else:
        # Format A: passcode prefix ending with :/
        passcode_match = re.match(r".*?\S+:/\s*", text)
        if passcode_match:
            text = text[passcode_match.end() :]

    # 5. If title has "..." or "…" followed by a longer description, keep only the short title
    text = text.strip()
    ellipsis_match = re.match(r"(.+?(?:\.\.\.|…))\s+.+", text)
    if ellipsis_match:
        text = ellipsis_match.group(1)

    return text.strip()


def process(messages: list[dict]) -> list[dict]:
    """Main pipeline: extract URLs from text + cards → fetch titles + content.

    Each result dict has keys: url, title, source_type, content.
    """
    # Filter out bot (app) messages — they contain status text, not real content
    messages = [m for m in messages if m.get("sender_type") != "app"]

    # 1. Card messages: title + URL extracted directly (no HTTP fetch needed)
    card_results = extract_from_cards(messages)
    seen_urls = {r["url"] for r in card_results}

    results: list[dict] = []
    for r in card_results:
        url = r["url"]
        src_type = get_source_type(url)
        # For card URLs without content, fetch it if non-video
        content = ""
        if not is_video_url(url):
            print(f"  Fetching content for card URL {url} ...")
            _, content = fetch_title_and_content(url)
        results.append({
            "url": url,
            "title": r["title"],
            "source_type": src_type,
            "content": content,
        })

    if results:
        print(f"  Extracted {len(results)} URLs from card messages")

    # 2. Text messages: extract URLs, skip those already found in cards
    text_urls = [u for u in extract_urls(messages) if u not in seen_urls]

    # 3. Fetch/extract titles + content
    for url in text_urls:
        src_type = get_source_type(url)
        if _is_douyin_url(url):
            # Extract title from Douyin share text directly (no HTTP fetch)
            msg_text = _find_message_text_for_url(messages, url)
            title = extract_douyin_title(msg_text)
            content = ""
            print(f"  Douyin: {url}")
            print(f"    -> {title or '(no title)'}")
        else:
            print(f"  Fetching title+content for {url} ...")
            title, content = fetch_title_and_content(url)
            print(f"    -> {title or '(no title)'}")
        results.append({
            "url": url,
            "title": title,
            "source_type": src_type,
            "content": content,
        })

    return results
