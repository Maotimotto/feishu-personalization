"""CLS (财联社) scraper — 头条 depth API."""

from __future__ import annotations

from datetime import datetime

import httpx

from .config import CLS_API_URL, CLS_API_PARAMS, CLS_HEADERS
from .base import Article, BaseScraper


class CLSScraper(BaseScraper):
    source_name = "财联社"

    def _do_fetch(self) -> list[Article]:
        resp = httpx.get(
            CLS_API_URL,
            params=CLS_API_PARAMS,
            headers=CLS_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("errno") != 0:
            raise RuntimeError(f"CLS API error: {data.get('msg', 'unknown')}")

        inner = data.get("data", {})
        articles: list[Article] = []

        # Collect from top_article + depth_list
        top_articles = inner.get("top_article") or []
        depth_list = inner.get("depth_list") or []

        seen_ids: set[int] = set()

        for item in top_articles + depth_list:
            article_id = item.get("id")
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            if item.get("is_ad"):
                continue

            title = (item.get("title") or "").strip()
            if not title:
                continue

            brief = (item.get("brief") or "").strip()

            ctime = item.get("ctime", 0)
            published_at = ""
            if ctime:
                published_at = datetime.fromtimestamp(ctime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            url = f"https://www.cls.cn/detail/{article_id}" if article_id else ""
            external = item.get("external_link") or ""
            if external:
                url = external

            reading_num = item.get("reading_num", 0)
            author = item.get("source") or ""
            if isinstance(author, dict):
                author = author.get("name", "")

            subjects = item.get("subjects") or []
            tags = [s.get("subject_name", "") for s in subjects if isinstance(s, dict) and s.get("subject_name")]

            articles.append(
                Article(
                    source=self.source_name,
                    title=title,
                    url=url,
                    summary=brief,
                    published_at=published_at,
                    author=str(author),
                    hits=reading_num,
                    tags=tags,
                )
            )

        return articles
