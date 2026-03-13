"""Tests for agent/tools/create_feishu_doc.py — Markdown to Feishu block conversion."""

import pytest
from agent.tools.create_feishu_doc import (
    _text_run,
    _heading,
    _text_block,
    _bullet_block,
    _ordered_block,
    _quote_block,
    _divider_block,
    _parse_inline,
    markdown_to_blocks,
)


# ─── _text_run ──────────────────────────────────────────────────────────────

class TestTextRun:
    def test_plain_text(self):
        result = _text_run("hello")
        assert result == {
            "text_element_type": 1,
            "text_run": {"content": "hello"},
        }

    def test_bold(self):
        result = _text_run("bold", bold=True)
        assert result["text_run"]["text_element_style"]["bold"] is True

    def test_link(self):
        result = _text_run("click", link="https://example.com")
        assert result["text_run"]["text_element_style"]["link"]["url"] == "https://example.com"

    def test_bold_and_link(self):
        result = _text_run("click", bold=True, link="https://x.com")
        style = result["text_run"]["text_element_style"]
        assert style["bold"] is True
        assert style["link"]["url"] == "https://x.com"

    def test_no_style_when_empty(self):
        result = _text_run("plain")
        assert "text_element_style" not in result["text_run"]

    def test_empty_string(self):
        result = _text_run("")
        assert result["text_run"]["content"] == ""


# ─── _heading ───────────────────────────────────────────────────────────────

class TestHeading:
    @pytest.mark.parametrize("level,expected_type", [
        (1, 3), (2, 4), (3, 5), (4, 6), (5, 7), (6, 8),
    ])
    def test_heading_levels(self, level, expected_type):
        result = _heading(level, "Title")
        assert result["block_type"] == expected_type
        key = f"heading{level}"
        assert key in result
        assert result[key]["elements"][0]["text_run"]["content"] == "Title"


# ─── _parse_inline ──────────────────────────────────────────────────────────

class TestParseInline:
    def test_plain_text(self):
        elements = _parse_inline("hello world")
        assert len(elements) == 1
        assert elements[0]["text_run"]["content"] == "hello world"

    def test_bold_text(self):
        elements = _parse_inline("**bold**")
        assert len(elements) == 1
        assert elements[0]["text_run"]["content"] == "bold"
        assert elements[0]["text_run"]["text_element_style"]["bold"] is True

    def test_mixed_bold_and_plain(self):
        elements = _parse_inline("hello **world** end")
        assert len(elements) == 3
        assert elements[0]["text_run"]["content"] == "hello "
        assert elements[1]["text_run"]["content"] == "world"
        assert elements[1]["text_run"]["text_element_style"]["bold"] is True
        assert elements[2]["text_run"]["content"] == " end"

    def test_multiple_bold_segments(self):
        elements = _parse_inline("**a** and **b**")
        bold_elements = [e for e in elements if e["text_run"].get("text_element_style", {}).get("bold")]
        assert len(bold_elements) == 2

    def test_empty_string_returns_text_run(self):
        elements = _parse_inline("")
        assert len(elements) == 1
        assert elements[0]["text_run"]["content"] == ""

    def test_unclosed_bold_markers_treated_as_plain(self):
        """BUG CHECK: unclosed ** should be treated as plain text."""
        elements = _parse_inline("**unclosed bold")
        # The regex won't match unclosed bold, so it should be plain text
        assert len(elements) == 1
        assert elements[0]["text_run"]["content"] == "**unclosed bold"

    def test_adjacent_bold(self):
        elements = _parse_inline("**a****b**")
        bold_elements = [e for e in elements if e["text_run"].get("text_element_style", {}).get("bold")]
        assert len(bold_elements) == 2


# ─── markdown_to_blocks ────────────────────────────────────────────────────

class TestMarkdownToBlocks:
    def test_empty_input(self):
        assert markdown_to_blocks("") == []

    def test_blank_lines_only(self):
        assert markdown_to_blocks("\n\n\n") == []

    def test_single_paragraph(self):
        blocks = markdown_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 2  # text block

    def test_heading_h1(self):
        blocks = markdown_to_blocks("# Title")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 3
        assert blocks[0]["heading1"]["elements"][0]["text_run"]["content"] == "Title"

    def test_heading_h2(self):
        blocks = markdown_to_blocks("## Subtitle")
        assert blocks[0]["block_type"] == 4

    def test_heading_h3(self):
        blocks = markdown_to_blocks("### Section")
        assert blocks[0]["block_type"] == 5

    def test_heading_h6(self):
        blocks = markdown_to_blocks("###### Deep")
        assert blocks[0]["block_type"] == 8

    def test_heading_no_space_is_paragraph(self):
        """'#NoSpace' without space after # should NOT be a heading."""
        blocks = markdown_to_blocks("#NoSpace")
        assert blocks[0]["block_type"] == 2  # paragraph, not heading

    def test_horizontal_rule_dash(self):
        blocks = markdown_to_blocks("---")
        assert blocks[0]["block_type"] == 22

    def test_horizontal_rule_asterisk(self):
        blocks = markdown_to_blocks("***")
        assert blocks[0]["block_type"] == 22

    def test_horizontal_rule_underscore(self):
        blocks = markdown_to_blocks("___")
        assert blocks[0]["block_type"] == 22

    def test_unordered_list_dash(self):
        blocks = markdown_to_blocks("- item one")
        assert blocks[0]["block_type"] == 13

    def test_unordered_list_asterisk(self):
        blocks = markdown_to_blocks("* item one")
        assert blocks[0]["block_type"] == 13

    def test_unordered_list_plus(self):
        blocks = markdown_to_blocks("+ item one")
        assert blocks[0]["block_type"] == 13

    def test_ordered_list(self):
        blocks = markdown_to_blocks("1. first item")
        assert blocks[0]["block_type"] == 12

    def test_ordered_list_multidigit(self):
        blocks = markdown_to_blocks("10. tenth item")
        assert blocks[0]["block_type"] == 12

    def test_blockquote(self):
        blocks = markdown_to_blocks("> quoted text")
        assert blocks[0]["block_type"] == 15

    def test_blockquote_no_space_is_paragraph(self):
        """> without space is NOT a blockquote."""
        blocks = markdown_to_blocks(">nospace")
        assert blocks[0]["block_type"] == 2  # paragraph

    def test_mixed_content(self):
        md = """# Title

Some paragraph text with **bold**.

- bullet 1
- bullet 2

1. ordered 1
2. ordered 2

---

> a quote"""
        blocks = markdown_to_blocks(md)
        types = [b["block_type"] for b in blocks]
        assert 3 in types   # heading1
        assert 2 in types   # text
        assert 13 in types  # bullet
        assert 12 in types  # ordered
        assert 22 in types  # divider
        assert 15 in types  # quote

    def test_bold_in_paragraph(self):
        blocks = markdown_to_blocks("hello **world** end")
        elements = blocks[0]["text"]["elements"]
        assert len(elements) == 3
        assert elements[1]["text_run"]["text_element_style"]["bold"] is True

    def test_bold_in_bullet(self):
        blocks = markdown_to_blocks("- **bold item**")
        elements = blocks[0]["bullet"]["elements"]
        assert elements[0]["text_run"]["text_element_style"]["bold"] is True

    def test_heading_bold_parsed(self):
        """FIXED: heading now uses _parse_inline, so bold in heading is correctly parsed."""
        blocks = markdown_to_blocks("## **Bold Title**")
        heading = blocks[0]["heading2"]["elements"][0]
        assert heading["text_run"]["content"] == "Bold Title"
        assert heading["text_run"]["text_element_style"]["bold"] is True

    def test_whitespace_lines_skipped(self):
        blocks = markdown_to_blocks("  \n\nhello\n  \n")
        assert len(blocks) == 1

    def test_indented_text_treated_as_paragraph(self):
        blocks = markdown_to_blocks("    indented code")
        assert blocks[0]["block_type"] == 2  # paragraph (no code block support)

    def test_many_headings_produces_correct_count(self):
        md = "\n".join(f"{'#' * i} Heading {i}" for i in range(1, 7))
        blocks = markdown_to_blocks(md)
        assert len(blocks) == 6
