"""Scheduled message gatherer — fetch chat history, save locally, upload to Feishu doc.

Usage:
    python gather.py

Reads CHAT_IDS and FETCH_DURATION from .env. For each chat, fetches messages,
saves to exports/{date}_{chat_id}.json, creates a Feishu document, and sends
the doc link back to the chat.

Cron example (daily at 18:00):
    0 18 * * * cd /path/to/feishu-bot && python gather.py >> /tmp/feishu-gather.log 2>&1
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx
import openpyxl

from bot import api_client, fetch_chat_messages, parse_duration, send_reply
from extract import process as extract_urls_with_titles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from dotenv import load_dotenv

load_dotenv()

CHAT_IDS: list[str] = [
    cid.strip()
    for cid in os.getenv("CHAT_IDS", "").split(",")
    if cid.strip()
]
FETCH_DURATION: str = os.getenv("FETCH_DURATION", "1d")
EXPORTS_DIR = Path(__file__).parent / "exports"

# ---------------------------------------------------------------------------
# Feishu Doc helpers (adapted from metals/feishu)
# ---------------------------------------------------------------------------

_API = "https://open.feishu.cn/open-apis"

_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


def _get_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    resp = httpx.post(
        f"{_API}/auth/v3/tenant_access_token/internal",
        json={
            "app_id": os.environ["FEISHU_APP_ID"],
            "app_secret": os.environ["FEISHU_APP_SECRET"],
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


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _set_org_editable(token: str, doc_type: str) -> None:
    """Grant all org members edit permission on a document.

    Args:
        token: The document/spreadsheet token.
        doc_type: "docx" for documents, "sheet" for spreadsheets.
    """
    resp = httpx.patch(
        f"{_API}/drive/v1/permissions/{token}/public",
        params={"type": doc_type},
        headers=_headers(),
        json={"link_share_entity": "tenant_editable"},
        timeout=15,
    )
    data = resp.json()
    if data.get("code", -1) != 0:
        print(f"Warning: set org permission failed: code={data.get('code')} msg={data.get('msg')}")
    else:
        print(f"Set org-editable permission for {doc_type} {token}")


def create_document(title: str) -> str:
    """Create a new Feishu document, return its document_id."""
    resp = httpx.post(
        f"{_API}/docx/v1/documents",
        headers=_headers(),
        json={"title": title},
        timeout=15,
    )
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(f"Create document failed: code={data['code']} msg={data['msg']}")
    doc_id = data["data"]["document"]["document_id"]
    _set_org_editable(doc_id, "docx")
    return doc_id


_BATCH = 50


def append_blocks(document_id: str, blocks: list[dict]) -> None:
    """Append blocks to document root, batching to stay under rate limits."""
    for i in range(0, len(blocks), _BATCH):
        batch = blocks[i : i + _BATCH]
        resp = httpx.post(
            f"{_API}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            headers=_headers(),
            json={"children": batch, "index": -1},
            timeout=15,
        )
        data = resp.json()
        if data["code"] != 0:
            raise RuntimeError(f"Append blocks failed: code={data['code']} msg={data['msg']}")
        if i + _BATCH < len(blocks):
            time.sleep(0.4)


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------


def _text_run(content: str) -> dict:
    return {"text_run": {"content": content}}


def _heading1(text: str) -> dict:
    return {"block_type": 3, "heading1": {"elements": [_text_run(text)]}}


def _heading2(text: str) -> dict:
    return {"block_type": 4, "heading2": {"elements": [_text_run(text)]}}


def _text_block(text: str) -> dict:
    return {"block_type": 2, "text": {"elements": [_text_run(text)]}}


# ---------------------------------------------------------------------------
# Spreadsheet helpers
# ---------------------------------------------------------------------------


def _display_title(item: dict) -> str:
    title = item.get("title", "")
    if title:
        return title
    src = item.get("source_type", "")
    if src:
        url = item.get("url", "")
        short = url.rstrip("/").rsplit("/", 1)[-1][:12] if url else ""
        return f"{src} {short}".strip()
    return item.get("url", "")


def build_classified_rows(url_results: list[dict]) -> list[list[str]]:
    from llm import classify_titles
    display_titles = [_display_title(r) for r in url_results]
    keyword_groups = classify_titles(display_titles)

    rows: list[list[str]] = [["热词", "选题", "来源链接", "来源类型", "文案"]]
    for keyword, indices in keyword_groups.items():
        first = True
        for idx in indices:
            item = url_results[idx]
            rows.append([
                keyword if first else "",
                display_titles[idx],
                item.get("url", ""),
                item.get("source_type", ""),
                item.get("content", ""),
            ])
            first = False
    return rows


def create_spreadsheet(title: str, url_results: list[dict]) -> str:
    """Create a Feishu spreadsheet with classified hot-word rows.

    Args:
        title: Spreadsheet title.
        url_results: List of dicts with keys: url, title, source_type, content.

    Returns:
        URL of the created spreadsheet.
    """
    # 1. Create spreadsheet
    resp = httpx.post(
        f"{_API}/sheets/v3/spreadsheets",
        headers=_headers(),
        json={"title": title},
        timeout=15,
    )
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(
            f"Create spreadsheet failed: code={data['code']} msg={data['msg']}"
        )
    token = data["data"]["spreadsheet"]["spreadsheet_token"]

    # 2. Set org-editable permission
    _set_org_editable(token, "sheet")

    # 3. Query default sheet ID
    resp = httpx.get(
        f"{_API}/sheets/v3/spreadsheets/{token}/sheets/query",
        headers=_headers(),
        timeout=15,
    )
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(
            f"Query sheets failed: code={data['code']} msg={data['msg']}"
        )
    sheet_id = data["data"]["sheets"][0]["sheet_id"]

    # 4. Rename sheet to today's date (e.g. "20260306") via v2 batch update
    today_short = datetime.now().strftime("%Y%m%d")
    resp = httpx.post(
        f"{_API}/sheets/v2/spreadsheets/{token}/sheets_batch_update",
        headers=_headers(),
        json={
            "requests": [
                {
                    "updateSheet": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": today_short,
                        }
                    }
                }
            ]
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code", -1) != 0:
        print(f"Warning: rename sheet failed: code={data.get('code')} msg={data.get('msg')}")

    # 5. Build classified rows
    rows = build_classified_rows(url_results)

    # 6. Write all data
    row_count = len(rows)
    range_str = f"{sheet_id}!A1:E{row_count}"

    resp = httpx.put(
        f"{_API}/sheets/v2/spreadsheets/{token}/values",
        headers=_headers(),
        json={"valueRange": {"range": range_str, "values": rows}},
        timeout=30,
    )
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(
            f"Write values failed: code={data['code']} msg={data['msg']}"
        )

    domain = os.getenv("FEISHU_DOMAIN", "feishu.cn")
    return f"https://{domain}/sheets/{token}"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _extract_text(content) -> str:
    """Best-effort extraction of displayable text from message content."""
    if isinstance(content, dict):
        return content.get("text", json.dumps(content, ensure_ascii=False))
    return str(content)


def _build_doc_blocks(messages: list[dict], date: str, start: str, end: str) -> list[dict]:
    """Build Feishu doc blocks from a list of messages."""
    blocks: list[dict] = [
        _heading1(f"Group Chat Summary \u2014 {date}"),
        _heading2(f"Time range: {start} ~ {end}"),
    ]
    for m in messages:
        ts = m.get("time", "")
        # Extract just HH:MM:SS from the full timestamp
        short_time = ts.split(" ", 1)[1] if " " in ts else ts
        sender = m.get("sender_id", "unknown")
        text = _extract_text(m.get("content"))
        blocks.append(_text_block(f"[{short_time}] {sender}: {text}"))
    return blocks


def gather_chat(chat_id: str, duration_seconds: int) -> None:
    """Fetch messages, save locally, create Feishu doc, send link."""
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n--- Chat {chat_id} ---")

    # 1. Fetch messages
    messages = fetch_chat_messages(chat_id, duration_seconds)
    print(f"Fetched {len(messages)} messages")

    if not messages:
        print("No messages found, skipping.")
        return

    # 2. Create Feishu document
    start_time = messages[0].get("time", "")
    end_time = messages[-1].get("time", "")
    doc_url = None

    try:
        title = f"Chat Summary \u2014 {today}"
        doc_id = create_document(title)
        blocks = _build_doc_blocks(messages, today, start_time, end_time)
        append_blocks(doc_id, blocks)
        doc_url = f"https://{os.getenv('FEISHU_DOMAIN', 'feishu.cn')}/docx/{doc_id}"
        print(f"Created doc: {doc_url}")

        # Send link to chat
        send_reply(chat_id, f"Daily summary ready: {doc_url}")
        print(f"Sent doc link to chat")
    except Exception as e:
        print(f"Doc creation failed (missing docx:document permission?): {e}")
        print("Local JSON export will still be saved.")

    # 3. Save locally (includes doc_url if created)
    export_data = {
        "chat_id": chat_id,
        "date": today,
        "duration": FETCH_DURATION,
        "doc_url": doc_url,
        "message_count": len(messages),
        "messages": messages,
    }
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    export_path = EXPORTS_DIR / f"{today}_{chat_id}.json"
    export_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved to {export_path}")

    # 4. Extract URLs + titles + content → Excel + Feishu spreadsheet
    print("Extracting URLs and fetching titles...")
    url_results = extract_urls_with_titles(messages)
    if url_results:
        rows = build_classified_rows(url_results)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = datetime.now().strftime("%Y%m%d")
        for row in rows:
            ws.append(row)
        xlsx_path = EXPORTS_DIR / f"{today}_{chat_id}.xlsx"
        wb.save(xlsx_path)
        print(f"Saved Excel to {xlsx_path} ({len(url_results)} URLs)")
    else:
        print("No URLs found in messages.")


def main() -> None:
    if not CHAT_IDS:
        print("Error: CHAT_IDS not set in .env")
        return

    duration_seconds = parse_duration(FETCH_DURATION)
    hours = duration_seconds / 3600
    print(f"Gathering messages — duration={FETCH_DURATION} ({hours:.1f}h), chats={len(CHAT_IDS)}")

    for chat_id in CHAT_IDS:
        gather_chat(chat_id, duration_seconds)

    print("\nDone.")


if __name__ == "__main__":
    main()
