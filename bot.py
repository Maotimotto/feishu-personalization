"""Feishu Bot — WebSocket 入口，个性化热点榜单生成。

触发方式：
1. 定时任务：每天早上 SCHEDULE_TIME，为所有达人生成个性化榜单
2. 群消息：@bot 更新XX的个性化榜单
"""

import json
import re
import threading
import time
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()

import lark_oapi as lark
import schedule
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from agent.pipeline import run_pipeline, list_creators
from agent.config import get_config
from agent.feishu import get_sdk_client, send_chat_text

config = get_config()
APP_ID = config["FEISHU_APP_ID"]
APP_SECRET = config["FEISHU_APP_SECRET"]

# Deduplication: track recently handled message IDs (max 256)
_handled_msgs: OrderedDict[str, None] = OrderedDict()
_HANDLED_MAX = 256

# Pipeline concurrency guard: only one pipeline runs at a time
_pipeline_lock = threading.Lock()

# Ignore messages created before the bot started
_boot_time_ms = int(time.time() * 1000)

# Pattern: 更新XX的个性化榜单
_UPDATE_PATTERN = re.compile(r"更新(.+?)的个性化榜单")


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds to a readable string like '1m30s'."""
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s}s" if m else f"{s}s"


def reply_to_message(message_id: str, text: str) -> None:
    """Reply to a specific message."""
    client = get_sdk_client()
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
    response = client.im.v1.message.reply(request)
    if not response.success():
        print(f"[reply] Error: code={response.code} msg={response.msg}")


# ---------------------------------------------------------------------------
# Scheduled pipeline — daily auto-run for all creators
# ---------------------------------------------------------------------------


def scheduled_pipeline_job() -> None:
    """定时任务：为所有达人生成个性化热点榜单。"""
    cfg = get_config()
    chat_ids = [c.strip() for c in cfg["CHAT_IDS"].split(",") if c.strip()]
    creators = list_creators()

    if not chat_ids:
        print("[scheduler] No CHAT_IDS configured, skipping")
        return
    if not creators:
        print("[scheduler] No creator prompt files found, skipping")
        return

    if not _pipeline_lock.acquire(blocking=False):
        print("[scheduler] Pipeline already running, skipping scheduled run")
        return

    try:
        for chat_id in chat_ids:
            for creator_name in creators:
                _run_for_creator(creator_name, chat_id)
    finally:
        _pipeline_lock.release()


def _run_for_creator(creator_name: str, chat_id: str) -> None:
    """Execute pipeline for one creator, send elapsed time when done."""
    start_time = time.time()

    try:
        result = run_pipeline(creator_name, chat_id)
        elapsed = _format_elapsed(time.time() - start_time)
        send_chat_text(chat_id, f"{creator_name} 的个性化热点榜单已完成！共耗时 {elapsed}")
        print(f"[scheduler] Pipeline done for {creator_name}: {result[:200]}...")
    except Exception as e:
        elapsed = _format_elapsed(time.time() - start_time)
        send_chat_text(chat_id, f"{creator_name} 的榜单生成失败（耗时 {elapsed}）: {e}")
        print(f"[scheduler] Pipeline error for {creator_name}: {e}")


def _scheduler_loop() -> None:
    """Background thread: check and run pending scheduled jobs."""
    while True:
        schedule.run_pending()
        time.sleep(30)


# ---------------------------------------------------------------------------
# WebSocket message handler
# ---------------------------------------------------------------------------


def on_message(data: P2ImMessageReceiveV1) -> None:
    try:
        event = data.event
        msg = event.message

        # Skip messages sent before the bot started
        create_ts = int(msg.create_time) if msg.create_time else 0
        if create_ts < _boot_time_ms:
            return

        # Deduplicate
        if msg.message_id in _handled_msgs:
            return
        _handled_msgs[msg.message_id] = None
        if len(_handled_msgs) > _HANDLED_MAX:
            _handled_msgs.popitem(last=False)

        chat_id = msg.chat_id
        chat_type = msg.chat_type
        message_id = msg.message_id
        message_type = msg.message_type
        content = json.loads(msg.content)

        if message_type != "text":
            return

        text = content.get("text", "").strip()

        # Remove @mentions in group chats
        if chat_type == "group" and msg.mentions:
            for mention in msg.mentions:
                text = text.replace(mention.key, "")
            text = text.strip()

        # Match: 更新XX的个性化榜单
        match = _UPDATE_PATTERN.search(text)
        if not match:
            return

        creator_name = match.group(1).strip()
        print(f"[msg] Request to update '{creator_name}' hotlist from chat {chat_id}")

        # Guard: reject if a pipeline is already running
        if not _pipeline_lock.acquire(blocking=False):
            reply_to_message(message_id, "当前正在生成中，请稍后再试～")
            return

        reply_to_message(message_id, f"收到！正在为 {creator_name} 生成个性化热点榜单...")

        # Run pipeline in a separate thread
        def _run_pipeline_task(cname, cid, mid):
            try:
                result = run_pipeline(cname, cid)
                print(f"[pipeline] Result: {result[:200]}...")
            except Exception as e:
                print(f"[pipeline] Error: {e}")
                reply_to_message(mid, f"生成失败: {e}")
            finally:
                _pipeline_lock.release()

        t = threading.Thread(
            target=_run_pipeline_task,
            args=(creator_name, chat_id, message_id),
            daemon=True,
        )
        t.start()

    except Exception as e:
        import traceback
        print(f"[on_message] Exception: {e}", flush=True)
        traceback.print_exc()


if __name__ == "__main__":
    # Register daily scheduled pipeline
    schedule_time = config.get("SCHEDULE_TIME", "08:00")
    creators = list_creators()
    schedule.every().day.at(schedule_time).do(
        lambda: threading.Thread(
            target=scheduled_pipeline_job, daemon=True
        ).start()
    )
    print(f"Scheduled pipeline at {schedule_time} daily")
    print(f"Available creators: {creators}")
    print(f"Chat IDs: {config['CHAT_IDS']}")

    # Start scheduler background thread
    threading.Thread(target=_scheduler_loop, daemon=True).start()

    # Start WebSocket event listener
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

    print("Starting Feishu bot...")
    print("Commands: @bot 更新{达人名}的个性化榜单")
    ws_client.start()
