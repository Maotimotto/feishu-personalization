"""CLS (财联社) 有声早报 scraper — fetch daily morning report from subject page."""

from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from .config import CLS_HEADERS
from .base import Article, BaseScraper


# 有声早报专题页
CLS_SUBJECT_URL = "https://www.cls.cn/subject/1151"
CLS_DETAIL_URL = "https://www.cls.cn/detail/{article_id}"


class CLSMorningScraper(BaseScraper):
    source_name = "财联社早报"

    def _do_fetch(self) -> list[Article]:
        today = datetime.now().strftime("%Y-%m-%d")

        # Step 1: fetch subject page and extract __NEXT_DATA__
        resp = httpx.get(CLS_SUBJECT_URL, headers=CLS_HEADERS, timeout=15)
        resp.raise_for_status()

        articles_data = self._extract_articles_from_next_data(resp.text)
        if not articles_data:
            raise RuntimeError("无法从有声早报专题页提取文章列表")

        # Step 2: find article matching today's date
        target_article = None
        for item in articles_data:
            article_time = item.get("article_time", 0)
            if not article_time:
                continue
            article_date = datetime.fromtimestamp(article_time).strftime("%Y-%m-%d")
            if article_date == today:
                target_article = item
                break

        if not target_article:
            raise RuntimeError(f"未找到日期为 {today} 的早报文章")

        article_id = target_article["article_id"]
        detail_url = CLS_DETAIL_URL.format(article_id=article_id)

        # Step 3: fetch article detail page
        detail_resp = httpx.get(detail_url, headers=CLS_HEADERS, timeout=15)
        detail_resp.raise_for_status()

        detail = self._extract_detail_from_next_data(detail_resp.text)
        if not detail:
            raise RuntimeError(f"无法从文章详情页提取内容: {detail_url}")

        # Step 4: parse content HTML to plain text
        content_html = detail.get("content", "")
        content_text = self._html_to_text(content_html)

        title = detail.get("title", target_article.get("article_title", ""))
        brief = detail.get("brief", target_article.get("article_brief", ""))
        ctime = detail.get("ctime", target_article.get("article_time", 0))
        published_at = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S") if ctime else ""
        reading_num = detail.get("readingNum", 0)

        author_info = detail.get("author", {})
        author = author_info.get("name", "财联社") if isinstance(author_info, dict) else "财联社"

        articles = [
            Article(
                source=self.source_name,
                title=title,
                url=detail_url,
                summary=brief,
                published_at=published_at,
                author=author,
                hits=reading_num,
                tags=["有声早报"],
            )
        ]

        # Also parse individual news items from the content
        news_items = self._parse_news_items(content_text, detail_url)
        articles.extend(news_items)

        return articles

    @staticmethod
    def _extract_articles_from_next_data(html: str) -> list[dict]:
        """Extract article list from __NEXT_DATA__ in subject page."""
        match = re.search(r"__NEXT_DATA__.*?>(.*?)</script>", html)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        page_props = data.get("props", {}).get("initialProps", {}).get("pageProps", {})
        subject_detail = page_props.get("subjectDetail", {})
        return subject_detail.get("articles", [])

    @staticmethod
    def _extract_detail_from_next_data(html: str) -> dict:
        """Extract article detail from __NEXT_DATA__ in detail page."""
        match = re.search(r"__NEXT_DATA__.*?>(.*?)</script>", html)
        if not match:
            return {}
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

        return (
            data.get("props", {})
            .get("initialState", {})
            .get("detail", {})
            .get("articleDetail", {})
        )

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Strip HTML tags to get plain text, preserving paragraph breaks."""
        # Replace <p> and <br> with newlines
        text = re.sub(r"<br\s*/?>", "\n", html)
        text = re.sub(r"</p>", "\n", text)
        # Remove all remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Clean up whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _parse_news_items(self, content_text: str, source_url: str) -> list[Article]:
        """Parse numbered news items from the morning report content."""
        articles = []
        # Match patterns like "1、...", "2、..." within sections
        lines = content_text.split("\n")

        current_section = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Detect section headers (bold text that was in <strong> tags)
            if line in ("宏观新闻", "行业新闻", "公司新闻", "环球市场", "投资机会参考"):
                current_section = line
                continue

            # Match numbered items
            match = re.match(r"^(\d+)[、．.]\s*(.+)", line)
            if match and current_section:
                item_text = match.group(2).strip()
                # Use first sentence or first 80 chars as title
                title_match = re.match(r"^(.+?)[。；;！!]", item_text)
                title = title_match.group(1) if title_match else item_text[:80]

                articles.append(
                    Article(
                        source=self.source_name,
                        title=f"[{current_section}] {title}",
                        url=source_url,
                        summary=item_text[:200],
                        published_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        author="财联社",
                        tags=["有声早报", current_section],
                    )
                )

        return articles
