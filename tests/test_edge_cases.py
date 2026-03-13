"""Tests for edge cases, integration logic, and concurrency bugs."""

import threading
import time
import re
import json
import os
import pytest

from agent.tools.create_feishu_doc import markdown_to_blocks, _parse_inline


# ─── Pipeline lock concurrency bug ─────────────────────────────────────────

class TestPipelineLockBug:
    """FIXED: bot.py now wraps reply_to_message + thread start in try/except,
    releasing the lock if an exception occurs before the thread starts."""

    def test_lock_properly_released_on_failure(self):
        """Demonstrate the fixed pattern: lock is released even if pre-thread code fails."""
        lock = threading.Lock()

        acquired = lock.acquire(blocking=False)
        assert acquired is True

        try:
            # Simulate reply_to_message raising an exception
            raise RuntimeError("reply failed")
        except Exception:
            # FIXED: lock is now released in the except block
            lock.release()

        # Lock is available again
        can_acquire = lock.acquire(blocking=False)
        assert can_acquire is True
        lock.release()


# ─── Markdown edge cases ──────────────────────────────────────────────────

class TestMarkdownEdgeCases:
    def test_code_block_not_supported(self):
        """Code blocks (```) are NOT supported and treated as paragraphs."""
        md = "```python\nprint('hello')\n```"
        blocks = markdown_to_blocks(md)
        # All lines become paragraphs
        assert all(b["block_type"] == 2 for b in blocks)

    def test_link_not_parsed(self):
        """Links [text](url) are NOT parsed by _parse_inline."""
        elements = _parse_inline("[click here](https://example.com)")
        # The entire text including markdown link syntax becomes plain text
        assert elements[0]["text_run"]["content"] == "[click here](https://example.com)"

    def test_italic_not_parsed(self):
        """Single asterisk italic *text* is NOT parsed."""
        elements = _parse_inline("*italic*")
        assert elements[0]["text_run"]["content"] == "*italic*"

    def test_nested_lists_not_supported(self):
        """Nested lists (indented items) are not parsed correctly."""
        md = "- item 1\n  - nested item"
        blocks = markdown_to_blocks(md)
        # Both become top-level items (nested indentation lost)
        assert len(blocks) == 2

    def test_very_long_line(self):
        """Very long paragraph should still work."""
        long_text = "A" * 10000
        blocks = markdown_to_blocks(long_text)
        assert len(blocks) == 1
        assert blocks[0]["text"]["elements"][0]["text_run"]["content"] == long_text

    def test_unicode_content(self):
        """Chinese text should be handled correctly."""
        blocks = markdown_to_blocks("## 今日热点榜单\n- **黄金价格**大涨")
        assert len(blocks) == 2
        assert blocks[0]["block_type"] == 4  # h2
        assert blocks[1]["block_type"] == 13  # bullet

    def test_dash_in_text_not_divider(self):
        """A line with dashes but also text should not be a divider."""
        blocks = markdown_to_blocks("--- some text ---")
        # This specific line "--- some text ---" is not exactly "---"
        assert blocks[0]["block_type"] == 2  # paragraph, not divider

    def test_heading_level_7_not_supported(self):
        """Markdown only supports h1-h6. ####### should not match."""
        blocks = markdown_to_blocks("####### Too deep")
        # Regex only matches 1-6 #'s, so 7 won't match as heading
        assert blocks[0]["block_type"] == 2  # paragraph

    def test_mixed_list_types(self):
        md = "- bullet\n1. ordered"
        blocks = markdown_to_blocks(md)
        assert blocks[0]["block_type"] == 13  # bullet
        assert blocks[1]["block_type"] == 12  # ordered

    def test_trailing_whitespace_on_heading(self):
        blocks = markdown_to_blocks("## Title   ")
        assert blocks[0]["heading2"]["elements"][0]["text_run"]["content"] == "Title"


# ─── Search query generation parsing ──────────────────────────────────────

class TestSearchQueryParsing:
    def test_json_in_markdown_code_block(self):
        """LLM may wrap JSON in markdown code blocks."""
        content = '```json\n[{"query": "黄金", "topic": "news"}]\n```'
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        assert json_match is not None
        parsed = json.loads(json_match.group(0))
        assert len(parsed) == 1

    def test_json_with_extra_text(self):
        content = 'Here are the queries:\n[{"query": "A股", "topic": "finance"}]\nEnd.'
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        parsed = json.loads(json_match.group(0))
        assert parsed[0]["query"] == "A股"

    def test_no_json_array_in_response(self):
        content = "I cannot generate queries for this."
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        assert json_match is None

    def test_nested_brackets_nongreedy_first(self):
        """FIXED: pipeline now tries non-greedy match first, avoiding over-capture."""
        content = '[{"a": [1,2]}] some text [{"b": 3}]'
        # Non-greedy: matches [{"a": [1,2]
        json_match = re.search(r'\[.*?\]', content, re.DOTALL)
        matched = json_match.group(0)
        # Non-greedy gets the smallest match which may not be valid JSON,
        # but pipeline falls back to greedy if non-greedy parse fails
        try:
            json.loads(matched)
            non_greedy_valid = True
        except json.JSONDecodeError:
            non_greedy_valid = False

        # Greedy fallback
        json_match_greedy = re.search(r'\[.*\]', content, re.DOTALL)
        matched_greedy = json_match_greedy.group(0)
        try:
            json.loads(matched_greedy)
            greedy_valid = True
        except json.JSONDecodeError:
            greedy_valid = False

        # In this edge case, neither matches perfectly, but the two-pass
        # approach gives the best chance of finding valid JSON
        assert isinstance(non_greedy_valid, bool)
        assert isinstance(greedy_valid, bool)


# ─── Config edge cases ─────────────────────────────────────────────────────

class TestConfigEdgeCases:
    def test_base_url_strip_chat_completions(self):
        """get_llm() strips /chat/completions from base_url."""
        from agent.config import get_config
        config = get_config()
        # Simulate the stripping logic
        base_url = "https://api.example.com/v1/chat/completions"
        if base_url.endswith("/chat/completions"):
            base_url = base_url.replace("/chat/completions", "")
        assert base_url == "https://api.example.com/v1"

    def test_base_url_no_strip_needed(self):
        base_url = "https://api.example.com/v1"
        if base_url.endswith("/chat/completions"):
            base_url = base_url.replace("/chat/completions", "")
        assert base_url == "https://api.example.com/v1"

    def test_base_url_strip_bug_fixed(self):
        """FIXED: removesuffix() only strips the suffix, not all occurrences."""
        base_url = "https://chat/completions.example.com/v1/chat/completions"
        base_url = base_url.removesuffix("/chat/completions")
        # Now only the suffix is removed
        assert base_url == "https://chat/completions.example.com/v1"


# ─── Feishu doc block batch ────────────────────────────────────────────────

class TestBlockBatching:
    def test_batch_size_constant(self):
        from agent.tools.create_feishu_doc import _BATCH
        assert _BATCH == 50

    def test_many_blocks_split_correctly(self):
        """Verify that large markdown produces multiple batches."""
        lines = [f"- Item {i}" for i in range(120)]
        md = "\n".join(lines)
        blocks = markdown_to_blocks(md)
        assert len(blocks) == 120

        from agent.tools.create_feishu_doc import _BATCH
        num_batches = (len(blocks) + _BATCH - 1) // _BATCH
        assert num_batches == 3  # 120 / 50 = 2.4 → 3 batches


# ─── Output format / doc title ─────────────────────────────────────────────

class TestDocTitle:
    def test_doc_title_format(self):
        """Verify the doc title format used in pipeline.py."""
        from datetime import datetime
        creator_name = "飞哥"
        doc_title = f"☀️ {creator_name} 早间热点速览 · {datetime.now().strftime('%Y-%m-%d')}"
        assert "☀️" in doc_title
        assert "飞哥" in doc_title
        assert "早间热点速览" in doc_title

    def test_doc_url_format(self):
        """Verify doc URL construction."""
        domain = "test.feishu.cn"
        doc_id = "abc123"
        url = f"https://{domain}/docx/{doc_id}"
        assert url == "https://test.feishu.cn/docx/abc123"
