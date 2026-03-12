"""Keyword matching to tag precious-metals-related articles."""

from __future__ import annotations

import re

from .config import PRECIOUS_METALS_KEYWORDS
from .base import Article

# Build a single compiled regex from all keywords (case-insensitive)
_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in PRECIOUS_METALS_KEYWORDS),
    re.IGNORECASE,
)


def tag_precious_metals(articles: list[Article]) -> list[Article]:
    """Scan each article's title + summary for precious metals keywords.
    Sets is_precious_metals=True and adds matched keywords to tags.
    All articles are kept; matching ones are just tagged."""
    for article in articles:
        text = f"{article.title} {article.summary}"
        matches = _PATTERN.findall(text)
        if matches:
            article.is_precious_metals = True
            # Deduplicate matched keywords (preserve order)
            seen = set()
            for m in matches:
                lower = m.lower() if m.isascii() else m
                if lower not in seen:
                    seen.add(lower)
                    article.tags.append(m)
    return articles
