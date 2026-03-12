"""Eastmoney (东方财富) 资讯精华 scraper — JSONP API."""

from __future__ import annotations

import json
import re

import httpx

from .config import (
    EASTMONEY_NEWS_API_URL,
    EASTMONEY_NEWS_PARAMS,
    EASTMONEY_NEWS_HEADERS,
)
from .base import Article, BaseScraper


class EastmoneyNewsScraper(BaseScraper):
    source_name = "东方财富"

    def _do_fetch(self) -> list[Article]:
        resp = httpx.get(
            EASTMONEY_NEWS_API_URL,
            params=EASTMONEY_NEWS_PARAMS,
            headers=EASTMONEY_NEWS_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.text

        match = re.search(r"cb\((\{.*\})\)", text, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
        else:
            data = json.loads(text)

        items = (data.get("data") or {}).get("list") or []
        articles: list[Article] = []

        for item in items:
            title = (item.get("title") or "").strip()
            if not title:
                continue

            summary = (item.get("summary") or "").strip()
            show_time = item.get("showTime") or ""
            url = item.get("url") or ""
            media = item.get("mediaName") or ""

            articles.append(
                Article(
                    source=self.source_name,
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=show_time,
                    author=media,
                )
            )

        return articles
