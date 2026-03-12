"""Base scraper and Article dataclass."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    source: str
    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    author: str = ""
    hits: int = 0
    tags: list[str] = field(default_factory=list)
    is_precious_metals: bool = False


class BaseScraper:
    """Base class that wraps fetch() in error handling."""

    source_name: str = ""

    def fetch(self) -> tuple[list[Article], list[str]]:
        """Return (articles, errors). Catches all exceptions so one source
        failing doesn't crash others."""
        try:
            articles = self._do_fetch()
            return articles, []
        except Exception as e:
            tb = traceback.format_exc()
            error_msg = f"[{self.source_name}] {type(e).__name__}: {e}\n{tb}"
            return [], [error_msg]

    def _do_fetch(self) -> list[Article]:
        raise NotImplementedError
