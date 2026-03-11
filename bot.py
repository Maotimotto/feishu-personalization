"""Feishu Bot — WebSocket 入口，使用 Pipeline 直接代码流处理命令。"""

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

from agent.pipeline import run_pipeline
from agent.config import get_config
from agent.feishu import get_sdk_client, send_chat_text, update_message_text

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

_NOTIFY_PREFIX = "时间不多啦～我要开始整理你们发送的链接咯～叮叮当当🔨～"


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
# Scheduled pipeline — daily auto-run
# ---------------------------------------------------------------------------


def scheduled_pipeline_job() -> None:
    """定时任务：为所有配置群聊执行 pipeline，实时更新进度消息。"""
    cfg = get_config()
    chat_ids = [c.strip() for c in cfg["CHAT_IDS"].split(",") if c.strip()]
    duration = cfg.get("FETCH_DURATION", "1d")

    if not chat_ids:
        print("[scheduler] No CHAT_IDS configured, skipping")
        return

    if not _pipeline_lock.acquire(blocking=False):
        print("[scheduler] Pipeline already running, skipping scheduled run")
        return

    try:
        for chat_id in chat_ids:
            _run_scheduled_for_chat(chat_id, duration)
    finally:
        _pipeline_lock.release()


def _run_scheduled_for_chat(chat_id: str, duration: str) -> None:
    """Execute pipeline for one chat with progress notification."""
    start_time = time.time()

    # Send initial notification
    notify_text = f"{_NOTIFY_PREFIX}数据整理中···已耗时 0s"
    msg_id = send_chat_text(chat_id, notify_text)

    # Background thread to update elapsed time every 15s
    stop_event = threading.Event()

    def _progress_updater(mid: str, t0: float, stop: threading.Event) -> None:
        while not stop.wait(15):
            elapsed = _format_elapsed(time.time() - t0)
            update_message_text(
                mid, f"{_NOTIFY_PREFIX}数据整理中···已耗时 {elapsed}"
            )

    if msg_id:
        updater = threading.Thread(
            target=_progress_updater,
            args=(msg_id, start_time, stop_event),
            daemon=True,
        )
        updater.start()

    try:
        result = run_pipeline(chat_id, duration)
        print(f"[scheduler] Pipeline done for {chat_id}: {result[:200]}...")
    except Exception as e:
        print(f"[scheduler] Pipeline error for {chat_id}: {e}")
    finally:
        stop_event.set()
        if msg_id:
            elapsed = _format_elapsed(time.time() - start_time)
            update_message_text(
                msg_id, f"{_NOTIFY_PREFIX}整理完成！共耗时 {elapsed} ✅"
            )


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
            print(f"[skip] Ignoring pre-boot message {msg.message_id}")
            return

        # Deduplicate
        if msg.message_id in _handled_msgs:
            print(f"[dedup] Skipping already handled message {msg.message_id}")
            return
        _handled_msgs[msg.message_id] = None
        if len(_handled_msgs) > _HANDLED_MAX:
            _handled_msgs.popitem(last=False)

        chat_id = msg.chat_id
        chat_type = msg.chat_type
        message_id = msg.message_id
        message_type = msg.message_type
        content = json.loads(msg.content)

        print(f"[{chat_type}] chat={chat_id} type={message_type}")

        if message_type == "text":
            text = content.get("text", "").strip()

            # Remove @mentions in group chats
            if chat_type == "group" and msg.mentions:
                for mention in msg.mentions:
                    text = text.replace(mention.key, "")
                text = text.strip()

            if text.startswith("/fetch"):
                arg = text[len("/fetch"):].strip()
                duration = arg if arg else "1d"

                # Guard: reject if a pipeline is already running
                if not _pipeline_lock.acquire(blocking=False):
                    reply_to_message(message_id, "当前数据正在整理中，请勿重复整理～")
                    return

                reply_to_message(message_id, "正在获取消息并整理链接...")

                # Run pipeline in a separate thread to avoid blocking WebSocket
                def _run_pipeline_task(cid, dur, mid):
                    try:
                        result = run_pipeline(cid, dur)
                        print(f"[pipeline] Result: {result[:200]}...")
                    except Exception as e:
                        print(f"[pipeline] Error: {e}")
                        reply_to_message(mid, f"处理失败: {e}")
                    finally:
                        _pipeline_lock.release()

                t = threading.Thread(
                    target=_run_pipeline_task,
                    args=(chat_id, duration, message_id),
                    daemon=True,
                )
                t.start()
    except Exception as e:
        import traceback
        print(f"[on_message] Exception: {e}", flush=True)
        traceback.print_exc()


if __name__ == "__main__":
    # Register daily scheduled pipeline
    schedule_time = config.get("SCHEDULE_TIME", "10:20")
    schedule.every().day.at(schedule_time).do(
        lambda: threading.Thread(
            target=scheduled_pipeline_job, daemon=True
        ).start()
    )
    print(f"Scheduled pipeline at {schedule_time} daily for CHAT_IDS={config['CHAT_IDS']}")

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

    print("Starting Feishu bot (WebSocket + Pipeline)...")
    print("Commands: @bot /fetch [duration]")
    ws_client.start()
