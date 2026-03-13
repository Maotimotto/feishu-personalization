"""Tool: 创建飞书文档 — 将 Markdown 内容写入飞书文档。"""

from __future__ import annotations

import json
import re
import time

import httpx

from ..config import get_config
from ..feishu import api_headers, set_org_editable

_API = "https://open.feishu.cn/open-apis"
_BATCH = 50


# ─── Feishu block builders ───────────────────────────────────────────────────

def _text_run(content: str, bold: bool = False, link: str = "") -> dict:
    """Create a text_run element."""
    style: dict = {}
    if bold:
        style["bold"] = True
    if link:
        style["link"] = {"url": link}
    element: dict = {
        "text_run": {"content": content},
    }
    if style:
        element["text_run"]["text_element_style"] = style
    return element


def _heading(level: int, text: str) -> dict:
    """Create a heading block (level 1-9 → block_type 3-11)."""
    block_type = 2 + level  # heading1=3, heading2=4, heading3=5, ...
    key = f"heading{level}"
    return {"block_type": block_type, key: {"elements": _parse_inline(text)}}


def _text_block(elements: list[dict]) -> dict:
    """Create a text (paragraph) block."""
    return {"block_type": 2, "text": {"elements": elements}}


def _bullet_block(elements: list[dict]) -> dict:
    """Create a bullet list item block."""
    return {"block_type": 13, "bullet": {"elements": elements}}


def _ordered_block(elements: list[dict]) -> dict:
    """Create an ordered list item block."""
    return {"block_type": 12, "ordered": {"elements": elements}}


def _quote_block(elements: list[dict]) -> dict:
    """Create a quote block."""
    return {"block_type": 15, "quote": {"elements": elements}}


def _divider_block() -> dict:
    """Create a horizontal divider block."""
    return {"block_type": 22, "divider": {}}


# ─── Markdown → Feishu Blocks ────────────────────────────────────────────────

def _parse_inline(text: str) -> list[dict]:
    """Parse inline markdown (bold) into text_run elements."""
    elements = []
    parts = re.split(r'(\*\*[^*]+\*\*)', text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            elements.append(_text_run(part[2:-2], bold=True))
        else:
            elements.append(_text_run(part))
    return elements if elements else [_text_run(text)]


def markdown_to_blocks(md: str) -> list[dict]:
    """Convert markdown text to a list of Feishu document blocks."""
    blocks: list[dict] = []
    lines = md.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Empty line → skip
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            blocks.append(_divider_block())
            i += 1
            continue

        # Headings
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            blocks.append(_heading(level, text))
            i += 1
            continue

        # Blockquote
        if stripped.startswith("> "):
            quote_text = stripped[2:]
            blocks.append(_quote_block(_parse_inline(quote_text)))
            i += 1
            continue

        # Unordered list
        list_match = re.match(r'^[-*+]\s+(.+)$', stripped)
        if list_match:
            blocks.append(_bullet_block(_parse_inline(list_match.group(1))))
            i += 1
            continue

        # Ordered list
        ordered_match = re.match(r'^\d+\.\s+(.+)$', stripped)
        if ordered_match:
            blocks.append(_ordered_block(_parse_inline(ordered_match.group(1))))
            i += 1
            continue

        # Regular paragraph
        blocks.append(_text_block(_parse_inline(stripped)))
        i += 1

    return blocks


# ─── Feishu API ──────────────────────────────────────────────────────────────

def _create_document(title: str) -> str:
    """Create a Feishu document, return document_id."""
    resp = httpx.post(
        f"{_API}/docx/v1/documents",
        headers=api_headers(),
        json={"title": title},
        timeout=15,
    )
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(f"Create document failed: code={data['code']} msg={data['msg']}")
    doc_id = data["data"]["document"]["document_id"]
    set_org_editable(doc_id, "docx")
    return doc_id


def _append_blocks(document_id: str, blocks: list[dict]) -> None:
    """Append blocks to document root, batching to stay under rate limits."""
    for i in range(0, len(blocks), _BATCH):
        batch = blocks[i : i + _BATCH]
        resp = httpx.post(
            f"{_API}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            headers=api_headers(),
            json={"children": batch, "index": -1},
            timeout=15,
        )
        data = resp.json()
        if data["code"] != 0:
            raise RuntimeError(f"Append blocks failed: code={data['code']} msg={data['msg']}")
        if i + _BATCH < len(blocks):
            time.sleep(0.4)


def create_feishu_doc_from_markdown(title: str, markdown_content: str) -> dict:
    """创建飞书文档，将 Markdown 内容转换为飞书文档格式。

    Args:
        title: 文档标题。
        markdown_content: Markdown 格式的内容。

    Returns:
        dict with doc_url and doc_id, or error.
    """
    config = get_config()

    blocks = markdown_to_blocks(markdown_content)
    if not blocks:
        return {"error": "没有内容可以创建文档"}

    try:
        doc_id = _create_document(title)
        _append_blocks(doc_id, blocks)
        domain = config["FEISHU_DOMAIN"]
        doc_url = f"https://{domain}/docx/{doc_id}"
        return {"doc_url": doc_url, "doc_id": doc_id}
    except Exception as e:
        return {"error": str(e)}
