"""Tool: 从飞书群聊获取消息历史。"""

from __future__ import annotations

import json
import re
from datetime import datetime

from langchain_core.tools import tool
from lark_oapi.api.im.v1 import ListMessageRequest, ListMessageResponse

from ..config import get_config
from ..feishu import get_sdk_client


def _parse_duration(text: str) -> int:
    """Parse a duration string like '2h', '30m', '1d' into seconds."""
    text = text.strip().lower()
    match = re.match(r"^(\d+)\s*(h|m|d)$", text)
    if not match:
        return 24 * 3600
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    return 24 * 3600


@tool
def fetch_chat_messages(chat_id: str, duration: str = "1d") -> str:
    """从飞书群聊中获取指定时间段内的消息。这是大多数任务的第一步。

    ## 何时使用
    - 用户要求获取/汇总/分析群聊消息时，这通常是工作流的起点
    - 用户发送 /fetch 命令时
    - 需要了解群聊中讨论了什么内容时

    ## 何时不用
    - 已经获取过消息且数据仍然有效时，不要重复调用
    - 用户只是在闲聊或问问题，不涉及群聊消息分析时

    ## 输出去向
    输出的 JSON 直接传给 extract_urls 提取链接，或传给 create_feishu_doc 创建文档。

    ## 注意
    - chat_id 必须以 "oc_" 开头，如果用户未指定，使用环境变量 CHAT_IDS 中的值
    - duration 支持 "30m"（分钟）、"2h"（小时）、"1d"（天），默认 1 天
    - 自动过滤已删除消息和机器人消息
    - 消息量可能很大，获取后先检查 message_count，如果为 0 应提前告知用户

    Args:
        chat_id: 飞书群聊 ID（以 oc_ 开头）。
        duration: 时间范围，如 "2h"（2小时）、"30m"（30分钟）、"1d"（1天）。默认 1d。

    Returns:
        JSON 字符串，包含 chat_id、duration、hours、message_count 和 messages 列表。
    """
    import time

    duration_seconds = _parse_duration(duration)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - duration_seconds * 1000
    start_ts = str(start_ms // 1000)
    end_ts = str(now_ms // 1000)

    client = get_sdk_client()
    all_messages = []
    page_token = None

    while True:
        builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .start_time(start_ts)
            .end_time(end_ts)
            .sort_type("ByCreateTimeAsc")
            .page_size(50)
        )
        if page_token:
            builder = builder.page_token(page_token)

        request = builder.build()
        response: ListMessageResponse = client.im.v1.message.list(request)

        if not response.success():
            return json.dumps(
                {"error": f"API error: code={response.code} msg={response.msg}"},
                ensure_ascii=False,
            )

        items = response.data.items or []
        for item in items:
            if item.deleted:
                continue
            if item.sender and item.sender.sender_type == "app":
                continue
            content_str = item.body.content if item.body else ""
            try:
                content = json.loads(content_str)
            except (json.JSONDecodeError, TypeError):
                content = content_str

            all_messages.append({
                "message_id": item.message_id,
                "sender_id": item.sender.id if item.sender else "unknown",
                "sender_type": item.sender.sender_type if item.sender else "unknown",
                "msg_type": item.msg_type,
                "content": content,
                "create_time": item.create_time,
                "time": datetime.fromtimestamp(
                    int(item.create_time) / 1000
                ).strftime("%Y-%m-%d %H:%M:%S") if item.create_time else "",
            })

        if not response.data.has_more:
            break
        page_token = response.data.page_token

    hours = duration_seconds / 3600
    result = {
        "chat_id": chat_id,
        "duration": duration,
        "hours": hours,
        "message_count": len(all_messages),
        "messages": all_messages,
    }
    return json.dumps(result, ensure_ascii=False)
