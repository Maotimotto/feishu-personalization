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

from agent.pipeline import run_pipeline, list_creators, find_prompt_file
from agent.config import get_config
from agent.feishu import get_sdk_client, send_card_message, update_card_message

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


# ---------------------------------------------------------------------------
# ProgressCard — 飞书卡片实时进度展示
# ---------------------------------------------------------------------------


class ProgressCard:
    """Manages a Feishu interactive card that shows pipeline progress in real time."""

    def __init__(self, chat_id: str, creator_name: str) -> None:
        self.chat_id = chat_id
        self.creator_name = creator_name
        self.message_id: str | None = None
        self.logs: list[str] = []

    # ── card JSON builders ────────────────────────────────────────────────

    def _build_card(
        self,
        title: str,
        color: str,
        footer_elements: list[dict] | None = None,
    ) -> dict:
        elements: list[dict] = []

        # Log lines
        if self.logs:
            elements.append({
                "tag": "markdown",
                "content": "\n".join(self.logs),
            })

        # Divider + footer (doc link, etc.)
        if footer_elements:
            elements.append({"tag": "hr"})
            elements.extend(footer_elements)

        return {
            "header": {
                "template": color,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }

    # ── public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Send the initial progress card to the chat."""
        card = self._build_card(
            title=f"⏳ {self.creator_name} 个性化热点榜单生成中...",
            color="blue",
        )
        self.message_id = send_card_message(self.chat_id, card)

    def log(self, msg: str) -> None:
        """Append a progress line and update the card."""
        self.logs.append(f"✅ {msg}")
        if not self.message_id:
            return
        card = self._build_card(
            title=f"⏳ {self.creator_name} 个性化热点榜单生成中...",
            color="blue",
        )
        update_card_message(self.message_id, card)

    def finish(self, doc_url: str, elapsed: str) -> None:
        """Update the card to a success state with the document link."""
        card = self._build_card(
            title=f"✅ {self.creator_name} 个性化热点榜单已完成 · {elapsed}",
            color="green",
            footer_elements=[{
                "tag": "markdown",
                "content": f"📋 查看文档: [{doc_url}]({doc_url})",
            }],
        )
        if self.message_id:
            update_card_message(self.message_id, card)
        else:
            send_card_message(self.chat_id, card)

    def fail(self, error: str, elapsed: str) -> None:
        """Update the card to a failure state."""
        self.logs.append(f"❌ {error}")
        card = self._build_card(
            title=f"❌ {self.creator_name} 个性化热点榜单生成失败 · {elapsed}",
            color="red",
        )
        if self.message_id:
            update_card_message(self.message_id, card)
        else:
            send_card_message(self.chat_id, card)


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
    """Execute pipeline for one creator with a real-time progress card."""
    card = ProgressCard(chat_id, creator_name)
    card.start()
    start_time = time.time()

    try:
        result = run_pipeline(creator_name, chat_id, on_progress=card.log)
        elapsed = _format_elapsed(time.time() - start_time)
        if result.startswith("http"):
            card.finish(result, elapsed)
        else:
            card.fail(result, elapsed)
        print(f"[scheduler] Pipeline done for {creator_name}: {result[:200]}...")
    except Exception as e:
        elapsed = _format_elapsed(time.time() - start_time)
        card.fail(str(e), elapsed)
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

        # Match: 更新XX的个性化榜单 (find all occurrences)
        matches = _UPDATE_PATTERN.findall(text)
        if not matches:
            return

        # Resolve matched names to known creators
        known = list_creators()
        creators_to_update: list[str] = []

        for raw_name in matches:
            raw_name = raw_name.strip()
            if find_prompt_file(raw_name):
                creators_to_update.append(raw_name)
            else:
                # Check all known creators as substring (handles "飞哥和Trader" etc.)
                for c in known:
                    if c in raw_name:
                        creators_to_update.append(c)

        # Deduplicate while preserving order
        creators_to_update = list(dict.fromkeys(creators_to_update))

        if not creators_to_update:
            raw = "、".join(m.strip() for m in matches)
            available = "、".join(known) if known else "无"
            reply_to_message(message_id, f"未找到达人「{raw}」，当前可用达人：{available}")
            return

        names_str = "、".join(creators_to_update)
        print(f"[msg] Request to update [{names_str}] hotlist from chat {chat_id}")

        # Guard: reject if a pipeline is already running
        if not _pipeline_lock.acquire(blocking=False):
            reply_to_message(message_id, "当前正在生成中，请稍后再试～")
            return

        # Run pipeline for each creator sequentially in a separate thread
        def _run_pipeline_task(creators, cid, mid):
            try:
                for cname in creators:
                    card = ProgressCard(cid, cname)
                    card.start()
                    start_time = time.time()
                    try:
                        result = run_pipeline(cname, cid, on_progress=card.log)
                        elapsed = _format_elapsed(time.time() - start_time)
                        if result.startswith("http"):
                            card.finish(result, elapsed)
                        else:
                            card.fail(result, elapsed)
                        print(f"[pipeline] Result for {cname}: {result[:200]}...")
                    except Exception as e:
                        elapsed = _format_elapsed(time.time() - start_time)
                        card.fail(str(e), elapsed)
                        print(f"[pipeline] Error for {cname}: {e}")
            finally:
                _pipeline_lock.release()

        try:
            reply_to_message(message_id, f"收到！正在为 {names_str} 生成个性化热点榜单...")
            t = threading.Thread(
                target=_run_pipeline_task,
                args=(creators_to_update, chat_id, message_id),
                daemon=True,
            )
            t.start()
        except Exception:
            _pipeline_lock.release()
            raise

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
