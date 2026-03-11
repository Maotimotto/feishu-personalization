"""Tool: 创建飞书文档。"""

from __future__ import annotations

import json
import os
import time

import httpx
from langchain_core.tools import tool

from ..config import get_config
from ..feishu import api_headers, set_org_editable

_API = "https://open.feishu.cn/open-apis"
_BATCH = 50


# Block builders

def _text_run(content: str) -> dict:
    return {"text_run": {"content": content}}


def _heading1(text: str) -> dict:
    return {"block_type": 3, "heading1": {"elements": [_text_run(text)]}}


def _heading2(text: str) -> dict:
    return {"block_type": 4, "heading2": {"elements": [_text_run(text)]}}


def _text_block(text: str) -> dict:
    return {"block_type": 2, "text": {"elements": [_text_run(text)]}}


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


def _extract_text(content) -> str:
    if isinstance(content, dict):
        return content.get("text", json.dumps(content, ensure_ascii=False))
    return str(content)


@tool
def create_feishu_doc(messages_json: str, title: str = "") -> str:
    """创建飞书文档，将群聊消息格式化为带时间戳的聊天记录文档。

    ## 何时使用
    - 用户要求创建消息摘要文档时
    - 需要将群聊消息归档保存为文档时
    - 注意：这是创建「消息记录文档」，不是「链接分类表格」

    ## 何时不用
    - 用户要求的是链接分类汇总 → 应使用 create_feishu_spreadsheet
    - 消息列表为空时（会返回错误）

    ## 输入来源
    直接接收 fetch_chat_messages 的完整 JSON 输出。

    ## 输出去向
    返回飞书文档 URL，可通过 send_feishu_message 发送到群聊。

    ## 与 create_feishu_spreadsheet 的区别
    - 本工具：创建文档，记录原始消息流水（时间戳 + 发送者 + 内容）
    - create_feishu_spreadsheet：创建表格，按热词分类展示链接汇总

    Args:
        messages_json: fetch_chat_messages 的完整 JSON 输出。
        title: 文档标题。默认为 "Chat Summary — {日期}"。

    Returns:
        JSON 字符串，包含 doc_url、doc_id 和 message_count。
    """
    data = json.loads(messages_json)
    messages = data.get("messages", data) if isinstance(data, dict) else data

    if not messages:
        return json.dumps({"error": "没有消息可以创建文档"}, ensure_ascii=False)

    config = get_config()
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    if not title:
        title = f"Chat Summary — {today}"

    start_time = messages[0].get("time", "")
    end_time = messages[-1].get("time", "")

    blocks: list[dict] = [
        _heading1(f"Group Chat Summary — {today}"),
        _heading2(f"Time range: {start_time} ~ {end_time}"),
    ]
    for m in messages:
        ts = m.get("time", "")
        short_time = ts.split(" ", 1)[1] if " " in ts else ts
        sender = m.get("sender_id", "unknown")
        text = _extract_text(m.get("content"))
        blocks.append(_text_block(f"[{short_time}] {sender}: {text}"))

    try:
        doc_id = _create_document(title)
        _append_blocks(doc_id, blocks)
        domain = config["FEISHU_DOMAIN"]
        doc_url = f"https://{domain}/docx/{doc_id}"
        return json.dumps({
            "doc_url": doc_url,
            "doc_id": doc_id,
            "message_count": len(messages),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
