"""Jin10 (金十) scraper — HTTP + BeautifulSoup."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from .config import JIN10_URL, JIN10_HEADERS
from .base import Article, BaseScraper


class Jin10Scraper(BaseScraper):
    source_name = "金十"

    def _do_fetch(self) -> list[Article]:
        resp = httpx.get(JIN10_URL, headers=JIN10_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.jin10-news-list-item[data-id]")

        articles: list[Article] = []
        seen_ids: set[str] = set()

        for item in items:
            data_id = item.get("data-id", "")
            if not data_id or data_id in seen_ids:
                continue
            seen_ids.add(data_id)

            title_el = item.select_one("p.jin10-news-list-item-title")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            a_el = item.select_one("a[href]")
            url = a_el.get("href", "") if a_el else ""
            if not url:
                url = f"https://xnews.jin10.com/details/{data_id}"

            intro_el = item.select_one("p.jin10-news-list-item-intro")
            summary = intro_el.get_text(strip=True) if intro_el else ""

            time_el = item.select_one("p.jin10-news-list-item-time")
            published_at = time_el.get_text(strip=True) if time_el else ""

            articles.append(
                Article(
                    source=self.source_name,
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=published_at,
                )
            )

        return articles
