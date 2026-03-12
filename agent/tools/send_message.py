"""发送飞书消息。"""

from __future__ import annotations

import json

from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from ..feishu import get_sdk_client


def send_feishu_message(chat_id: str, text: str, reply_to: str = "") -> dict:
    """向飞书群聊发送文本消息。

    Args:
        chat_id: 飞书群聊 ID（以 oc_ 开头）。
        text: 要发送的文本内容。
        reply_to: 可选，要回复的消息 ID。

    Returns:
        dict with success status.
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
        return {"success": True, "chat_id": chat_id}
    else:
        return {"success": False, "error": f"code={response.code} msg={response.msg}"}
