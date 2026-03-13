"""金十数据全球财经早餐 — 抓取当日早餐文章链接及内容。"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

TOPIC_URL = "https://xnews.jin10.com/topic/343"
DETAIL_BASE = "https://xnews.jin10.com/details/"


def get_today_breakfast_url() -> str | None:
    """从金十专题页抓取当日全球财经早餐的文章链接。

    Returns:
        文章 URL（如 https://xnews.jin10.com/details/212704），
        未找到则返回 None。
    """
    today = datetime.now()
    today_str = f"{today.year}年{today.month}月{today.day}日"
    target_title = f"金十数据全球财经早餐 | {today_str}"

    resp = httpx.get(TOPIC_URL, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # 方案1: 通过 data-id + 标题匹配
    # HTML 结构: <div data-id="212704" ... class="jin10-news-list-item ...">
    #            ...  <p class="jin10-news-list-item-title">\n金十数据全球财经早餐 | 2026年3月11日\n</p>
    pattern = re.compile(
        r'<div\s+data-id="(\d+)"[^>]*class="[^"]*jin10-news-list-item[^"]*"[^>]*>'
        r'.*?<p\s+class="jin10-news-list-item-title">\s*'
        + re.escape(target_title)
        + r"\s*</p>",
        re.DOTALL,
    )
    match = pattern.search(html)
    if match:
        data_id = match.group(1)
        return f"{DETAIL_BASE}{data_id}"

    # 方案2: 直接匹配 href 链接
    href_pattern = re.compile(
        r'<a\s+href="(' + re.escape(DETAIL_BASE) + r'\d+)"[^>]*>.*?'
        + re.escape(target_title),
        re.DOTALL,
    )
    href_match = href_pattern.search(html)
    if href_match:
        return href_match.group(1)

    return None


def fetch_breakfast_content(url: str) -> str | None:
    """抓取金十早餐文章页面的正文内容。

    Args:
        url: 文章详情页 URL，如 https://xnews.jin10.com/details/212704

    Returns:
        提取后的纯文本内容（保留段落换行），失败返回 None。
    """
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    content_div = soup.find("div", class_="jin10-news-cdetails-content")
    if not content_div:
        return None

    # 文章正文嵌套在内层 <body> 中
    inner = content_div.find("body") or content_div

    lines: list[str] = []
    for el in inner.find_all(["p", "h1", "h2", "h3", "h4"]):
        text = el.get_text(strip=True)
        if not text:
            continue
        # 跳过音频播放段落（仅跳过 <p>，保留 <h2> 标题）
        if el.name == "p" and "insert-audio" in (el.get("class") or []):
            continue
        # 为标题添加 Markdown 标记
        if el.name in ("h1", "h2", "h3", "h4"):
            prefix = "#" * int(el.name[1])
            lines.append(f"\n{prefix} {text}")
        else:
            lines.append(text)

    return "\n".join(lines).strip() if lines else None


def get_today_breakfast() -> dict[str, str | None]:
    """一步到位：获取今日金十全球财经早餐的链接和正文。

    Returns:
        {"url": "...", "content": "..."} — 任一字段可能为 None。
    """
    url = get_today_breakfast_url()
    if not url:
        return {"url": None, "content": None}

    content = fetch_breakfast_content(url)
    return {"url": url, "content": content}


if __name__ == "__main__":
    result = get_today_breakfast()
    if result["url"]:
        print(f"今日早餐链接: {result['url']}")
        print(f"\n{'='*60}\n")
        if result["content"]:
            print(result["content"])
        else:
            print("内容抓取失败")
    else:
        print("未找到今日全球财经早餐文章")
