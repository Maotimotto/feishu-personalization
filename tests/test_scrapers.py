"""Tests for agent/scrapers — filters, formatter, base classes, and config."""

import os
import re
import tempfile
import pytest

from agent.scrapers.base import Article, BaseScraper
from agent.scrapers.filters import tag_precious_metals, _PATTERN
from agent.scrapers.formatter import generate_report, _escape_md
from agent.scrapers.config import PRECIOUS_METALS_KEYWORDS


# ─── Article dataclass ──────────────────────────────────────────────────────

class TestArticle:
    def test_defaults(self):
        a = Article(source="test", title="Title", url="https://example.com")
        assert a.summary == ""
        assert a.published_at == ""
        assert a.author == ""
        assert a.hits == 0
        assert a.tags == []
        assert a.is_precious_metals is False

    def test_tags_are_independent(self):
        """Each Article should have its own tags list (mutable default)."""
        a1 = Article(source="s", title="t1", url="u1")
        a2 = Article(source="s", title="t2", url="u2")
        a1.tags.append("gold")
        assert "gold" not in a2.tags  # Should not share list reference


# ─── BaseScraper ────────────────────────────────────────────────────────────

class TestBaseScraper:
    def test_fetch_catches_exceptions(self):
        class FailScraper(BaseScraper):
            source_name = "fail"
            def _do_fetch(self):
                raise ValueError("test error")

        scraper = FailScraper()
        articles, errors = scraper.fetch()
        assert articles == []
        assert len(errors) == 1
        assert "ValueError" in errors[0]
        assert "test error" in errors[0]

    def test_fetch_returns_articles(self):
        class OkScraper(BaseScraper):
            source_name = "ok"
            def _do_fetch(self):
                return [Article(source="ok", title="News", url="https://example.com")]

        scraper = OkScraper()
        articles, errors = scraper.fetch()
        assert len(articles) == 1
        assert errors == []

    def test_base_do_fetch_raises(self):
        scraper = BaseScraper()
        articles, errors = scraper.fetch()
        assert articles == []
        assert len(errors) == 1
        assert "NotImplementedError" in errors[0]


# ─── tag_precious_metals ───────────────────────────────────────────────────

class TestTagPreciousMetals:
    def test_tags_matching_title(self):
        articles = [
            Article(source="test", title="黄金价格大涨", url="u"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is True
        assert "黄金" in result[0].tags

    def test_tags_matching_summary(self):
        articles = [
            Article(source="test", title="市场综述", url="u", summary="白银期货创新高"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is True

    def test_no_match_untagged(self):
        articles = [
            Article(source="test", title="A股大涨", url="u", summary="沪深300涨幅明显"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is False
        assert result[0].tags == []

    def test_multiple_keywords(self):
        articles = [
            Article(source="test", title="黄金白银齐涨", url="u"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is True
        assert len(result[0].tags) >= 2

    def test_english_keywords_case_insensitive(self):
        articles = [
            Article(source="test", title="GOLD prices surge", url="u"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is True

    def test_xauusd_keyword(self):
        articles = [
            Article(source="test", title="XAUUSD breaks record", url="u"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is True

    def test_preserves_all_articles(self):
        """All articles should be kept, matching ones just get tagged."""
        articles = [
            Article(source="test", title="黄金", url="u1"),
            Article(source="test", title="股票", url="u2"),
            Article(source="test", title="白银", url="u3"),
        ]
        result = tag_precious_metals(articles)
        assert len(result) == 3

    def test_deduplicates_tags(self):
        """Same keyword appearing in title and summary should not duplicate tag."""
        articles = [
            Article(source="test", title="黄金黄金", url="u", summary="黄金涨价"),
        ]
        result = tag_precious_metals(articles)
        # "黄金" appears multiple times but should be deduplicated
        gold_count = result[0].tags.count("黄金")
        assert gold_count == 1

    def test_comex_keyword(self):
        articles = [
            Article(source="test", title="COMEX gold futures", url="u"),
        ]
        result = tag_precious_metals(articles)
        assert result[0].is_precious_metals is True

    def test_empty_list(self):
        result = tag_precious_metals([])
        assert result == []


# ─── _PATTERN regex ─────────────────────────────────────────────────────────

class TestPreciousMetalsPattern:
    def test_all_keywords_compile(self):
        """Verify the regex compiles and matches all keywords."""
        for kw in PRECIOUS_METALS_KEYWORDS:
            assert _PATTERN.search(kw) is not None, f"Keyword '{kw}' not matched by pattern"

    def test_case_insensitive(self):
        assert _PATTERN.search("Gold") is not None
        assert _PATTERN.search("SILVER") is not None
        assert _PATTERN.search("Precious Metal") is not None


# ─── _escape_md ─────────────────────────────────────────────────────────────

class TestEscapeMd:
    def test_pipe_escaped(self):
        assert _escape_md("a|b") == r"a\|b"

    def test_newline_replaced(self):
        assert _escape_md("line1\nline2") == "line1 line2"

    def test_both(self):
        assert _escape_md("a|b\nc|d") == r"a\|b c\|d"

    def test_no_special_chars(self):
        assert _escape_md("plain text") == "plain text"

    def test_empty_string(self):
        assert _escape_md("") == ""


# ─── generate_report ───────────────────────────────────────────────────────

class TestGenerateReport:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_generates_markdown_file(self):
        articles = [
            Article(source="财联社", title="Test Article", url="https://example.com",
                    summary="Summary text", published_at="2026-03-13", author="Author"),
        ]
        # Monkey-patch OUTPUT_DIR
        import agent.scrapers.formatter as fmt_mod
        original = fmt_mod.OUTPUT_DIR
        fmt_mod.OUTPUT_DIR = self.tmpdir
        try:
            filepath = generate_report(articles, [])
            assert os.path.exists(filepath)
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            assert "Test Article" in content
            assert "财联社" in content
            assert "总文章数" in content
        finally:
            fmt_mod.OUTPUT_DIR = original

    def test_report_with_precious_metals(self):
        articles = [
            Article(source="test", title="Gold Price Up", url="u",
                    is_precious_metals=True, tags=["gold"]),
        ]
        import agent.scrapers.formatter as fmt_mod
        original = fmt_mod.OUTPUT_DIR
        fmt_mod.OUTPUT_DIR = self.tmpdir
        try:
            filepath = generate_report(articles, [])
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            assert "贵金属要闻" in content
            assert "贵金属相关" in content
        finally:
            fmt_mod.OUTPUT_DIR = original

    def test_report_with_errors(self):
        import agent.scrapers.formatter as fmt_mod
        original = fmt_mod.OUTPUT_DIR
        fmt_mod.OUTPUT_DIR = self.tmpdir
        try:
            filepath = generate_report([], ["Error: something failed"])
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            assert "错误与警告" in content
            assert "Error: something failed" in content
        finally:
            fmt_mod.OUTPUT_DIR = original

    def test_empty_articles(self):
        import agent.scrapers.formatter as fmt_mod
        original = fmt_mod.OUTPUT_DIR
        fmt_mod.OUTPUT_DIR = self.tmpdir
        try:
            filepath = generate_report([], [])
            assert os.path.exists(filepath)
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            assert "总文章数**: 0" in content
        finally:
            fmt_mod.OUTPUT_DIR = original

    def test_pipe_in_title_escaped(self):
        articles = [
            Article(source="test", title="A|B", url="u"),
        ]
        import agent.scrapers.formatter as fmt_mod
        original = fmt_mod.OUTPUT_DIR
        fmt_mod.OUTPUT_DIR = self.tmpdir
        try:
            filepath = generate_report(articles, [])
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            # Pipe in title should be escaped
            assert r"A\|B" in content
        finally:
            fmt_mod.OUTPUT_DIR = original
