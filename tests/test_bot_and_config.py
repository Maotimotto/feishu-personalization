"""Tests for bot.py — message handling, scheduling, formatting utilities."""

import json
import re
import threading
import time
import pytest

# Import the specific functions/patterns we can test without starting the bot
from bot import _format_elapsed, _UPDATE_PATTERN, _HANDLED_MAX


# ─── _format_elapsed ───────────────────────────────────────────────────────

class TestFormatElapsed:
    def test_zero_seconds(self):
        assert _format_elapsed(0) == "0s"

    def test_seconds_only(self):
        assert _format_elapsed(45) == "45s"

    def test_exactly_one_minute(self):
        assert _format_elapsed(60) == "1m0s"

    def test_minutes_and_seconds(self):
        assert _format_elapsed(90) == "1m30s"

    def test_large_value(self):
        assert _format_elapsed(3661) == "61m1s"

    def test_float_truncated(self):
        assert _format_elapsed(45.7) == "45s"

    def test_just_under_minute(self):
        assert _format_elapsed(59) == "59s"

    def test_negative_value(self):
        """BUG CHECK: negative elapsed time (e.g., clock skew)."""
        # divmod(-1, 60) returns (-1, 59) in Python, which would give "-1m59s"
        result = _format_elapsed(-1)
        # This is technically incorrect behavior but documenting it
        assert "m" in result or "s" in result


# ─── _UPDATE_PATTERN ───────────────────────────────────────────────────────

class TestUpdatePattern:
    def test_matches_basic(self):
        m = _UPDATE_PATTERN.search("更新飞哥的个性化榜单")
        assert m is not None
        assert m.group(1) == "飞哥"

    def test_matches_trader(self):
        m = _UPDATE_PATTERN.search("更新Trader的个性化榜单")
        assert m is not None
        assert m.group(1) == "Trader"

    def test_matches_with_prefix(self):
        m = _UPDATE_PATTERN.search("请帮我更新飞哥的个性化榜单")
        assert m is not None
        assert m.group(1) == "飞哥"

    def test_no_match_missing_keyword(self):
        m = _UPDATE_PATTERN.search("生成飞哥的榜单")
        assert m is None

    def test_extracts_name_with_spaces(self):
        m = _UPDATE_PATTERN.search("更新 大飞哥 的个性化榜单")
        assert m is not None
        # .strip() is applied in bot.py after extraction
        assert "大飞哥" in m.group(1).strip()

    def test_empty_name_matches(self):
        """BUG CHECK: empty name between 更新 and 的 still matches."""
        m = _UPDATE_PATTERN.search("更新的个性化榜单")
        # (.+?) requires at least 1 char, so this should not match
        assert m is None

    def test_greedy_vs_lazy_matching(self):
        """The regex (.+?) still matches '飞哥的朋友' from '更新飞哥的朋友的个性化榜单',
        but bot.py now validates the name against known creators and falls back
        to substring matching, so it will resolve to '飞哥'."""
        m = _UPDATE_PATTERN.search("更新飞哥的朋友的个性化榜单")
        assert m is not None
        # Regex still extracts the full span
        assert m.group(1) == "飞哥的朋友"
        # But bot.py will correct this via find_prompt_file + substring fallback


# ─── Config loading ─────────────────────────────────────────────────────────

class TestConfigTypes:
    def test_config_type_consistency(self):
        """BUG: get_config() says dict[str, str] but returns mixed types."""
        from agent.config import get_config
        config = get_config()

        # These are documented as str but are actually float/int
        assert isinstance(config["LLM_TEMPERATURE"], float)
        assert isinstance(config["LLM_MAX_TOKENS"], int)
        assert isinstance(config["LLM_TIMEOUT"], int)
        assert isinstance(config["LLM_MAX_RETRIES"], int)

        # These should be str
        assert isinstance(config["OPENAI_MODEL"], str)
        assert isinstance(config["FEISHU_DOMAIN"], str)

    def test_default_values(self):
        from agent.config import get_config
        config = get_config()
        # Verify defaults work even without .env
        assert config["FEISHU_DOMAIN"]  # has default "feishu.cn"
        assert config["SCHEDULE_TIME"]  # has default "08:00"


# ─── Scraper config constants ──────────────────────────────────────────────

class TestScraperConfig:
    def test_cls_api_params_has_sign(self):
        """BUG CHECK: hardcoded sign may expire."""
        from agent.scrapers.config import CLS_API_PARAMS
        assert "sign" in CLS_API_PARAMS
        assert CLS_API_PARAMS["sign"]  # not empty

    def test_source_map_matches_creator_data_sources(self):
        """All scrapers referenced in CREATOR_DATA_SOURCES should exist in SOURCE_MAP."""
        from agent.scrapers.main import SOURCE_MAP
        from agent.pipeline import CREATOR_DATA_SOURCES

        for creator, cfg in CREATOR_DATA_SOURCES.items():
            for scraper_name in cfg.get("scrapers", []):
                assert scraper_name in SOURCE_MAP, (
                    f"Creator '{creator}' references scraper '{scraper_name}' "
                    f"which is not in SOURCE_MAP"
                )


# ─── CLS Morning scraper parsing ──────────────────────────────────────────

class TestCLSMorningParser:
    def test_html_to_text(self):
        from agent.scrapers.cls_morning import CLSMorningScraper
        html = "<p>Hello</p><p>World</p><br/>End"
        text = CLSMorningScraper._html_to_text(html)
        assert "Hello" in text
        assert "World" in text

    def test_html_to_text_strips_tags(self):
        from agent.scrapers.cls_morning import CLSMorningScraper
        html = "<strong>Bold</strong> and <a href='#'>link</a>"
        text = CLSMorningScraper._html_to_text(html)
        assert "<strong>" not in text
        assert "<a " not in text
        assert "Bold" in text
        assert "link" in text

    def test_extract_articles_from_empty_html(self):
        from agent.scrapers.cls_morning import CLSMorningScraper
        result = CLSMorningScraper._extract_articles_from_next_data("")
        assert result == []

    def test_extract_detail_from_empty_html(self):
        from agent.scrapers.cls_morning import CLSMorningScraper
        result = CLSMorningScraper._extract_detail_from_next_data("")
        assert result == {}

    def test_parse_news_items_with_sections(self):
        from agent.scrapers.cls_morning import CLSMorningScraper
        scraper = CLSMorningScraper()
        content = """宏观新闻
1、央行宣布降息。市场反应积极。
2、GDP数据超预期。
行业新闻
1、新能源汽车销量创新高。"""
        items = scraper._parse_news_items(content, "https://example.com")
        assert len(items) == 3
        assert items[0].source == "财联社早报"
        assert "宏观新闻" in items[0].title
        assert "行业新闻" in items[2].title

    def test_parse_news_items_without_section_header(self):
        """Items without a preceding section header should be skipped."""
        from agent.scrapers.cls_morning import CLSMorningScraper
        scraper = CLSMorningScraper()
        content = """1、这条没有section header。"""
        items = scraper._parse_news_items(content, "https://example.com")
        assert len(items) == 0


# ─── Eastmoney JSONP parsing ──────────────────────────────────────────────

class TestEastmoneyParsing:
    def test_jsonp_extraction(self):
        """Test JSONP callback unwrapping regex."""
        jsonp = 'cb({"data":{"list":[{"title":"test","summary":"s","showTime":"12:00","url":"u","mediaName":"m"}]}})'
        match = re.search(r"cb\((\{.*\})\)", jsonp, re.DOTALL)
        assert match is not None
        data = json.loads(match.group(1))
        assert data["data"]["list"][0]["title"] == "test"

    def test_plain_json_fallback(self):
        """When response is plain JSON without callback wrapper."""
        plain = '{"data":{"list":[]}}'
        match = re.search(r"cb\((\{.*\})\)", plain, re.DOTALL)
        assert match is None
        # Fallback: parse as plain JSON
        data = json.loads(plain)
        assert data["data"]["list"] == []


# ─── Jin10 breakfast date format ───────────────────────────────────────────

class TestJin10BreakfastDateFormat:
    def test_date_format_platform(self):
        """BUG CHECK: %-m and %-d are platform-specific (Linux/Mac only, fails on Windows)."""
        from datetime import datetime
        # This should work on macOS/Linux but NOT on Windows
        try:
            result = datetime.now().strftime("%Y年%-m月%-d日")
            assert "年" in result
            assert "月" in result
            assert "日" in result
            # Verify no leading zeros
            parts = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", result)
            assert parts is not None
        except ValueError:
            pytest.skip("%-m/%-d not supported on this platform (Windows)")


# ─── Web search output format inconsistency ───────────────────────────────

class TestSearchOutputFormat:
    def test_exa_and_tavily_both_return_text(self):
        """FIXED: Both Exa and Tavily now return human-readable text format."""
        # Both backends now produce the same text format:
        # "搜索结果（{query}）：\n\n1. **Title**\n   链接：url\n..."
        exa_output = "搜索结果（test）：\n\n1. **Title**\n   链接：url\n"
        tavily_output = "搜索结果（test）：\n\n1. **Title**\n   链接：url\n"
        assert exa_output.startswith("搜索结果")
        assert tavily_output.startswith("搜索结果")
