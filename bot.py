import os
import re
import json
import time
from collections import OrderedDict
from datetime import datetime
from dotenv import load_dotenv
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ListMessageRequest,
    ListMessageResponse,
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageResponse,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

load_dotenv()

APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")

# HTTP client for API calls (fetching history, sending replies)
api_client = (
    lark.Client.builder()
    .app_id(APP_ID)
    .app_secret(APP_SECRET)
    .log_level(lark.LogLevel.DEBUG)
    .build()
)

# Deduplication: track recently handled message IDs (max 256)
_handled_msgs: OrderedDict[str, None] = OrderedDict()
_HANDLED_MAX = 256

# Ignore messages created before the bot started (queued while offline)
_boot_time_ms = int(time.time() * 1000)


def parse_duration(text: str) -> int:
    """Parse a duration string like '2h', '30m', '1d' into seconds.
    Defaults to 24h if not specified or unparseable."""
    text = text.strip().lower()
    match = re.match(r"^(\d+)\s*(h|m|d)$", text)
    if not match:
        return 24 * 3600  # default 24 hours
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    return 24 * 3600


def fetch_chat_messages(chat_id: str, duration_seconds: int) -> list[dict]:
    """Fetch all messages from a chat within the given time window.

    Returns a list of dicts with keys: sender_id, msg_type, content, time.
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - duration_seconds * 1000
    # API expects Unix timestamps as strings (in milliseconds? no — seconds with fractional)
    # Actually the Feishu API uses Unix timestamp strings in *seconds* (e.g. "1609296000")
    start_ts = str(start_ms // 1000)
    end_ts = str(now_ms // 1000)

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
        response: ListMessageResponse = api_client.im.v1.message.list(request)

        if not response.success():
            raise RuntimeError(f"API error: code={response.code} msg={response.msg}")

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

    return all_messages


def send_reply(chat_id: str, text: str) -> None:
    """Send a text message to a chat."""
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
    response: CreateMessageResponse = api_client.im.v1.message.create(request)
    if not response.success():
        print(f"[send] Error: code={response.code} msg={response.msg}")


def reply_to_message(message_id: str, text: str) -> None:
    """Reply to a specific message."""
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("text")
        .content(json.dumps({"text": text}))
        .build()
    )
    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    response = api_client.im.v1.message.reply(request)
    if not response.success():
        print(f"[reply] Error: code={response.code} msg={response.msg}")


def on_message(data: P2ImMessageReceiveV1) -> None:
    event = data.event
    msg = event.message

    # Skip messages sent before the bot started (queued while offline)
    create_ts = int(msg.create_time) if msg.create_time else 0
    if create_ts < _boot_time_ms:
        print(f"[skip] Ignoring pre-boot message {msg.message_id} (sent before bot started)")
        return

    # Deduplicate: Feishu WebSocket may deliver the same event multiple times
    if msg.message_id in _handled_msgs:
        print(f"[dedup] Skipping already handled message {msg.message_id}")
        return
    _handled_msgs[msg.message_id] = None
    if len(_handled_msgs) > _HANDLED_MAX:
        _handled_msgs.popitem(last=False)

    sender = event.sender.sender_id.open_id

    chat_type = msg.chat_type
    chat_id = msg.chat_id
    message_id = msg.message_id
    message_type = msg.message_type
    content = json.loads(msg.content)

    print(f"[{chat_type}] chat={chat_id} sender={sender}")
    print(f"  type={message_type} content={content}")

    # Handle /fetch command
    if message_type == "text":
        text = content.get("text", "").strip()

        # In group chats, remove @mentions using the precise mention keys
        if chat_type == "group" and msg.mentions:
            for mention in msg.mentions:
                text = text.replace(mention.key, "")
            text = text.strip()

        if text.startswith("/fetch"):
            arg = text[len("/fetch"):].strip()
            duration = parse_duration(arg) if arg else 24 * 3600
            hours = duration / 3600

            print(f"[fetch] Fetching messages from chat={chat_id} last {hours}h...")
            reply_to_message(message_id, "正在获取消息并整理链接...")

            try:
                messages = fetch_chat_messages(chat_id, duration)
            except Exception as e:
                print(f"[fetch] Error: {e}")
                reply_to_message(message_id, f"获取消息失败: {e}")
                return

            if not messages:
                reply_to_message(message_id, "该时间段内没有消息")
                return

            print(f"[fetch] Got {len(messages)} messages, extracting URLs...")

            from extract import process as extract_urls_with_titles
            url_results = extract_urls_with_titles(messages)

            if not url_results:
                reply_to_message(
                    message_id,
                    f"过去 {hours:.1f} 小时内共 {len(messages)} 条消息，未发现链接",
                )
                return

            print(f"[fetch] Found {len(url_results)} URLs, creating spreadsheet...")

            try:
                from gather import create_spreadsheet
                today = datetime.now().strftime("%Y-%m-%d")
                sheet_url = create_spreadsheet(f"{today} 热点事件", url_results)
                reply_to_message(
                    message_id,
                    f"链接汇总（{len(url_results)} 条）：{sheet_url}",
                )
            except Exception as e:
                print(f"[fetch] Spreadsheet creation failed: {e}")
                reply_to_message(message_id, f"表格创建失败: {e}")


if __name__ == "__main__":
    event_handler = (
        lark.EventDispatcherHandler
        .builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    ws_client = lark.ws.Client(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,
    )

    print("Starting Feishu bot (WebSocket)...")
    print("Commands: @bot /fetch [duration] — fetch messages (e.g. /fetch 2h, /fetch 1d)")
    ws_client.start()
