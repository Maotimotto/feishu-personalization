"""Futu (富途) scraper — Playwright headless browser."""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from .config import FUTU_URL
from .base import Article, BaseScraper


class FutuScraper(BaseScraper):
    source_name = "富途"

    def _do_fetch(self) -> list[Article]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(FUTU_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            articles = self._parse_dom(page)

            if not articles:
                try:
                    page.screenshot(path="output/futu-debug.png", full_page=True)
                except Exception:
                    pass

            browser.close()
            return articles

    def _parse_dom(self, page) -> list[Article]:
        """Parse articles from rendered DOM."""
        articles: list[Article] = []

        items = page.query_selector_all("a.market-item")
        if not items:
            return articles

        seen_titles: set[str] = set()

        for el in items:
            href = el.get_attribute("href") or ""
            if href and not href.startswith("http"):
                href = f"https://news.futunn.com{href}"

            title_el = el.query_selector(".market-item__title, h2")
            if not title_el:
                continue
            title = (title_el.inner_text() or "").strip()
            if not title or len(title) < 4:
                continue

            if title in seen_titles:
                continue
            seen_titles.add(title)

            author = ""
            time_text = ""
            footer_el = el.query_selector(".market-item__footer")
            if footer_el:
                footer_text = (footer_el.inner_text() or "").strip()
                footer_text = footer_text.replace("置顶", "").strip()
                parts = re.split(r"[·\n]", footer_text)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 2:
                    author = parts[0]
                    time_text = parts[-1]
                elif len(parts) == 1:
                    time_text = parts[0]

            articles.append(
                Article(
                    source=self.source_name,
                    title=title,
                    url=href,
                    published_at=time_text,
                    author=author,
                )
            )

        return articles
