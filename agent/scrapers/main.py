"""Main orchestrator — runs scrapers, applies filters, generates report."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .cls import CLSScraper
from .cls_morning import CLSMorningScraper
from .jin10 import Jin10Scraper
from .futu import FutuScraper
from .eastmoney_news import EastmoneyNewsScraper
from .base import Article, BaseScraper
from .filters import tag_precious_metals
from .formatter import generate_report


SOURCE_MAP = {
    "财联社": CLSScraper,
    "财联社早报": CLSMorningScraper,
    "金十": Jin10Scraper,
    "富途": FutuScraper,
    "东方财富": EastmoneyNewsScraper,
}

ALL_SOURCES = list(SOURCE_MAP.keys())


def run_scrapers(sources: list[str] | None = None) -> tuple[str, dict]:
    """Run scrapers for specified sources (or all), apply filters, generate report.

    Returns:
        (filepath, stats) where stats contains article counts and details.
    """
    if sources is None:
        sources = ALL_SOURCES

    all_articles: list[Article] = []
    all_errors: list[str] = []
    stats: dict = {"sources": {}, "total": 0, "precious_metals": 0}

    scrapers_to_run: list[BaseScraper] = []
    for name in sources:
        cls = SOURCE_MAP.get(name)
        if cls:
            scrapers_to_run.append(cls())
        else:
            all_errors.append(f"未知数据源：{name}")

    def scrape_single(scraper: BaseScraper) -> tuple[list[Article], list[str], str]:
        print(f"[{scraper.source_name}] 正在抓取...")
        articles, errors = scraper.fetch()
        print(f"[{scraper.source_name}] 获取 {len(articles)} 篇文章")
        return articles, errors, scraper.source_name

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(scrape_single, s): s for s in scrapers_to_run}
        for future in as_completed(futures):
            articles, errors, source_name = future.result()
            all_articles.extend(articles)
            all_errors.extend(errors)
            stats["sources"][source_name] = len(articles)
            time.sleep(0.5)

    tag_precious_metals(all_articles)

    stats["total"] = len(all_articles)
    stats["precious_metals"] = sum(1 for a in all_articles if a.is_precious_metals)

    filepath = generate_report(all_articles, all_errors)
    return filepath, stats


def main():
    filepath, stats = run_scrapers()
    print(f"\n共获取 {stats['total']} 篇文章")
    print(f"贵金属相关：{stats['precious_metals']} 篇")
    print(f"报告已生成：{filepath}")


if __name__ == "__main__":
    main()
