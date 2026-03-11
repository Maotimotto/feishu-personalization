"""Tool: 发送飞书消息。"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from ..feishu import get_sdk_client


@tool
def send_feishu_message(chat_id: str, text: str, reply_to: str = "") -> str:
    """向飞书群聊发送文本消息。

    ## 何时使用
    - 将处理结果（如表格 URL、文档 URL）发送到群聊时
    - 回复用户在群聊中的指令时
    - 这通常是工作流的最后一步，将成果交付给用户

    ## 何时不用
    - 在 LangGraph Studio 中调试时，不需要发送消息
    - 用户没有明确要求将结果发到群聊时

    ## 注意
    - 发送的是纯文本消息，不支持富文本或 Markdown
    - 消息内容应简洁，包含关键结果和链接即可
    - 如果有飞书文档/表格 URL，直接发送 URL 即可，飞书会自动渲染卡片预览

    Args:
        chat_id: 飞书群聊 ID（以 oc_ 开头）。
        text: 要发送的文本内容，建议简洁明了。
        reply_to: 可选，要回复的消息 ID。如果提供则回复该消息，否则直接发送到群聊。

    Returns:
        JSON 字符串，包含 success 状态和 chat_id（或 error 信息）。
    """
    client = get_sdk_client()

    if reply_to:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        request = (
            ReplyMessageRequest.builder()
            .message_id(reply_to)
            .request_body(body)
            .build()
        )
        response = client.im.v1.message.reply(request)
    else:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        response = client.im.v1.message.create(request)

    if response.success():
        return json.dumps({"success": True, "chat_id": chat_id}, ensure_ascii=False)
    else:
        return json.dumps(
            {"success": False, "error": f"code={response.code} msg={response.msg}"},
            ensure_ascii=False,
        )
