"""Tool: 从消息中提取 URL 链接。"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from langchain_core.tools import tool

# Domains to skip — internal admin/platform URLs
_SKIP_DOMAINS = {"open.feishu.cn", "open.larksuite.com"}

# Match http/https URLs — exclude CJK characters and fullwidth punctuation
_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")

# Markdown link: [text](url)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")


def _should_skip_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.lower() in _SKIP_DOMAINS


def _extract_from_text(messages: list[dict]) -> list[str]:
    """Extract deduplicated URLs from text-type messages."""
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
                continue
            seen.add(url)
            urls.append(url)
    return urls


def _extract_from_cards(messages: list[dict]) -> list[dict]:
    """Extract {url, title} pairs from interactive card messages."""
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

        header = content.get("header") or {}
        title_obj = header.get("title") or {}
        card_title = title_obj.get("content", "") if isinstance(title_obj, dict) else ""

        card_urls: list[str] = []
        for element in content.get("elements") or []:
            for action in element.get("actions") or []:
                url = action.get("url") or action.get("multi_url", {}).get("url")
                if url:
                    card_urls.append(url.rstrip(",.;:!?)>"))

            md_content = element.get("content", "")
            if isinstance(md_content, str):
                for m in _MD_LINK_RE.finditer(md_content):
                    card_urls.append(m.group(2).rstrip(",.;:!?)>"))
                for m in _URL_RE.finditer(md_content):
                    url = m.group().rstrip(",.;:!?)>")
                    if url not in card_urls:
                        card_urls.append(url)

            text_obj = element.get("text")
            if isinstance(text_obj, dict):
                href = text_obj.get("href")
                if href:
                    card_urls.append(href.rstrip(",.;:!?)>"))

        for url in card_urls:
            if not url or url in seen:
                continue
            if _should_skip_url(url):
                continue
            seen.add(url)
            results.append({"url": url, "title": card_title})

    return results


@tool
def extract_urls(messages_json: str) -> str:
    """从飞书消息列表中提取所有 URL 链接。

    同时处理文本消息（提取裸 URL）和卡片消息（提取按钮/Markdown 链接及卡片标题），
    自动去重，过滤飞书内部管理链接。

    ## 何时使用
    - 获取到群聊消息后，需要从中提取链接进行内容抓取和分类时
    - 这是链接汇总流程的第二步（fetch_chat_messages → extract_urls）

    ## 何时不用
    - 用户只需要消息摘要、不涉及链接分析时
    - 已经提取过链接且数据仍然有效时

    ## 输入来源
    直接接收 fetch_chat_messages 的完整 JSON 输出（包含 messages 字段）。

    ## 输出去向
    输出的 JSON 直接传给 fetch_url_content 获取每个链接的标题和内容。
    输出中同时包含 messages 字段，供下游工具（如抖音标题提取）使用。

    ## 注意
    - 提取后检查 url_count，如果为 0 应告知用户该时段群聊中没有分享链接
    - 卡片消息提取的链接会附带卡片标题，文本消息提取的链接标题为空

    Args:
        messages_json: fetch_chat_messages 的完整 JSON 输出。

    Returns:
        JSON 字符串，包含 url_count、card_count、text_count、urls 列表和 messages 列表。
    """
    data = json.loads(messages_json)
    messages = data.get("messages", data) if isinstance(data, dict) else data

    # Filter out bot messages
    messages = [m for m in messages if m.get("sender_type") != "app"]

    # Extract from cards (with titles)
    card_results = _extract_from_cards(messages)
    seen_urls = {r["url"] for r in card_results}

    results: list[dict] = []
    for r in card_results:
        results.append({"url": r["url"], "title": r["title"], "source": "card"})

    # Extract from text (URLs only)
    text_urls = [u for u in _extract_from_text(messages) if u not in seen_urls]
    for url in text_urls:
        results.append({"url": url, "title": "", "source": "text"})

    output = {
        "url_count": len(results),
        "card_count": len(card_results),
        "text_count": len(text_urls),
        "urls": results,
        "messages": messages,  # Pass through for downstream tools
    }
    return json.dumps(output, ensure_ascii=False)
