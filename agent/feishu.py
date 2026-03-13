"""Feishu API 客户端 — 共享的飞书 SDK client 和 HTTP token 管理。"""

from __future__ import annotations

import json
import os
import time

import httpx
import lark_oapi as lark

from .config import get_config

_API = "https://open.feishu.cn/open-apis"

# Lazy-initialized SDK client
_sdk_client: lark.Client | None = None


def get_sdk_client() -> lark.Client:
    """Get or create the lark-oapi SDK client (for WebSocket events + REST)."""
    global _sdk_client
    if _sdk_client is None:
        config = get_config()
        _sdk_client = (
            lark.Client.builder()
            .app_id(config["FEISHU_APP_ID"])
            .app_secret(config["FEISHU_APP_SECRET"])
            .log_level(lark.LogLevel.DEBUG)
            .build()
        )
    return _sdk_client


# HTTP token cache for direct API calls
_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


def get_token() -> str:
    """Get a valid tenant_access_token, refreshing if needed."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    config = get_config()
    resp = httpx.post(
        f"{_API}/auth/v3/tenant_access_token/internal",
        json={
            "app_id": config["FEISHU_APP_ID"],
            "app_secret": config["FEISHU_APP_SECRET"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(f"Feishu auth failed: {data['msg']}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + data["expire"]
    return _token_cache["token"]


def api_headers() -> dict[str, str]:
    """Return HTTP headers with a valid Bearer token."""
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def set_org_editable(token: str, doc_type: str) -> None:
    """Grant anyone-with-link read permission on a document.

    Args:
        token: The document/spreadsheet token.
        doc_type: "docx" for documents, "sheet" for spreadsheets.
    """
    resp = httpx.patch(
        f"{_API}/drive/v1/permissions/{token}/public",
        params={"type": doc_type},
        headers=api_headers(),
        json={"link_share_entity": "anyone_readable"},
        timeout=15,
    )
    data = resp.json()
    if data.get("code", -1) != 0:
        print(f"Warning: set permission failed: code={data.get('code')} msg={data.get('msg')}")
    else:
        print(f"Set anyone-readable permission for {doc_type} {token}")


def send_chat_text(chat_id: str, text: str) -> str | None:
    """Send a text message to a chat and return the message_id for later updates."""
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    client = get_sdk_client()
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
        return response.data.message_id
    print(f"[feishu] Send failed: code={response.code} msg={response.msg}")
    return None


def send_card_message(chat_id: str, card_content: dict) -> str | None:
    """Send an interactive card message to a chat and return message_id."""
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    client = get_sdk_client()
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("interactive")
        .content(json.dumps(card_content))
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
        return response.data.message_id
    print(f"[feishu] Send card failed: code={response.code} msg={response.msg}")
    return None


def update_card_message(message_id: str, card_content: dict) -> bool:
    """Update an existing card message by message_id."""
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

    client = get_sdk_client()
    body = (
        PatchMessageRequestBody.builder()
        .content(json.dumps(card_content))
        .build()
    )
    request = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    response = client.im.v1.message.patch(request)
    if response.success():
        return True
    print(f"[feishu] Update card failed: code={response.code} msg={response.msg}")
    return False
