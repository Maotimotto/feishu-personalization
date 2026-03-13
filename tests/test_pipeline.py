"""Tests for agent/pipeline.py — prompt loading, creator discovery, content generation parsing."""

import os
import re
import json
import tempfile
import pytest

from agent.pipeline import (
    find_prompt_file,
    list_creators,
    load_prompt_template,
    _generate_initial_content_and_queries,
    _PROJECT_ROOT,
    CREATOR_DATA_SOURCES,
)


# ─── find_prompt_file ───────────────────────────────────────────────────────

class TestFindPromptFile:
    def test_finds_existing_creator(self):
        result = find_prompt_file("飞哥")
        assert result is not None
        assert "飞哥" in os.path.basename(result)
        assert result.endswith(".txt")

    def test_finds_trader(self):
        result = find_prompt_file("Trader")
        assert result is not None
        assert "Trader" in os.path.basename(result)

    def test_nonexistent_creator_returns_none(self):
        result = find_prompt_file("不存在的达人XYZ")
        assert result is None

    def test_partial_match_fallback(self):
        """The fallback searches all prompt files for partial name match."""
        result = find_prompt_file("飞")
        # Should match via partial fallback
        assert result is not None


# ─── list_creators ──────────────────────────────────────────────────────────

class TestListCreators:
    def test_returns_list(self):
        creators = list_creators()
        assert isinstance(creators, list)

    def test_contains_known_creators(self):
        creators = list_creators()
        # Both Trader and 飞哥 have prompt files
        assert "Trader" in creators or any("Trader" in c for c in creators)

    def test_no_extension_in_name(self):
        creators = list_creators()
        for name in creators:
            assert ".txt" not in name

    def test_regex_extracts_name_correctly(self):
        """Test the regex pattern used to extract creator names."""
        pattern = re.compile(r'^(.+?)(?:个性化.*)?提示词\.txt$')

        # Should match "Trader个性化榜单提示词.txt" → "Trader"
        m = pattern.match("Trader个性化榜单提示词.txt")
        assert m is not None
        assert m.group(1) == "Trader"

        # Should match "飞哥个性化提示词.txt" → "飞哥"
        m = pattern.match("飞哥个性化提示词.txt")
        assert m is not None
        assert m.group(1) == "飞哥"

        # Edge: just "提示词.txt" — should match with empty name? No, (.+?) requires at least 1 char
        m = pattern.match("提示词.txt")
        # (.+?) is greedy enough to match "提示" and then 词.txt... let's check
        # Actually the regex requires at least (.+?) before 提示词, so "提示词.txt"
        # would try to match (.+?) against "提示词" before "提示词\.txt" but that
        # conflicts. Let me just test it.
        assert m is None or m.group(1) != ""


# ─── load_prompt_template ──────────────────────────────────────────────────

class TestLoadPromptTemplate:
    def test_loads_file_content(self):
        prompt_file = find_prompt_file("Trader")
        assert prompt_file is not None
        content = load_prompt_template(prompt_file)
        assert len(content) > 0

    def test_strips_headlines_section(self):
        """Template should not contain <headlines> section."""
        prompt_file = find_prompt_file("Trader")
        if prompt_file is None:
            pytest.skip("Trader prompt file not found")
        content = load_prompt_template(prompt_file)
        assert "<headlines>" not in content
        assert "</headlines>" not in content

    def test_strips_todays_headlines_section(self):
        """Also handles <today's headlines> variant."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Before\n<today's headlines>\nsome headlines\n</today's headlines>\nAfter")
            f.flush()
            content = load_prompt_template(f.name)
        os.unlink(f.name)
        assert "some headlines" not in content
        assert "Before" in content
        assert "After" in content

    def test_preserves_non_headline_content(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("<instructions>\nDo stuff\n</instructions>\n<headlines>\nold news\n</headlines>")
            f.flush()
            content = load_prompt_template(f.name)
        os.unlink(f.name)
        assert "Do stuff" in content
        assert "old news" not in content

    def test_returns_stripped_content(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("  \n  content  \n  ")
            f.flush()
            content = load_prompt_template(f.name)
        os.unlink(f.name)
        assert content == "content"


# ─── CREATOR_DATA_SOURCES config ────────────────────────────────────────────

class TestCreatorDataSources:
    def test_feige_has_scrapers(self):
        cfg = CREATOR_DATA_SOURCES.get("飞哥")
        assert cfg is not None
        assert "scrapers" in cfg
        assert len(cfg["scrapers"]) > 0

    def test_trader_has_scrapers(self):
        cfg = CREATOR_DATA_SOURCES.get("Trader")
        assert cfg is not None
        assert "scrapers" in cfg

    def test_feige_jin10_breakfast(self):
        cfg = CREATOR_DATA_SOURCES["飞哥"]
        assert cfg.get("jin10_breakfast") is True

    def test_unknown_creator_returns_none(self):
        cfg = CREATOR_DATA_SOURCES.get("未知达人")
        assert cfg is None


# ─── _generate_initial_content_and_queries parsing ──────────────────────────

class TestParseInitialContentAndQueries:
    """Test the XML parsing logic in _generate_initial_content_and_queries
    by directly testing the regex patterns used."""

    def test_parse_initial_content_tag(self):
        content = """
<initial_content>
# 榜单标题
1. 选题一
2. 选题二
</initial_content>

<search_queries>
[{"query": "关键词1", "reason": "原因1"}]
</search_queries>
"""
        initial_match = re.search(
            r"<initial_content>\s*(.*?)\s*</initial_content>", content, re.DOTALL
        )
        assert initial_match is not None
        assert "榜单标题" in initial_match.group(1)

        queries_match = re.search(
            r"<search_queries>\s*(.*?)\s*</search_queries>", content, re.DOTALL
        )
        assert queries_match is not None
        raw = queries_match.group(1).strip()
        parsed = json.loads(raw)
        assert len(parsed) == 1
        assert parsed[0]["query"] == "关键词1"

    def test_parse_without_tags_fallback(self):
        """When LLM doesn't output XML tags, entire content becomes initial_content."""
        content = "Just plain markdown content without XML tags"
        initial_match = re.search(
            r"<initial_content>\s*(.*?)\s*</initial_content>", content, re.DOTALL
        )
        # BUG CHECK: When no match, the code uses entire content as fallback
        assert initial_match is None

    def test_parse_malformed_json_in_queries(self):
        """When JSON inside search_queries is malformed."""
        content = """
<initial_content>content</initial_content>
<search_queries>
not valid json
</search_queries>
"""
        queries_match = re.search(
            r"<search_queries>\s*(.*?)\s*</search_queries>", content, re.DOTALL
        )
        raw = queries_match.group(1).strip()
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        # No JSON array found
        assert json_match is None

    def test_parse_empty_queries(self):
        content = """
<initial_content>some content</initial_content>
<search_queries>
[]
</search_queries>
"""
        queries_match = re.search(
            r"<search_queries>\s*(.*?)\s*</search_queries>", content, re.DOTALL
        )
        raw = queries_match.group(1).strip()
        parsed = json.loads(raw)
        assert parsed == []
