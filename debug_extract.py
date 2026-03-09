"""Debug script: show all URLs gathered and filtered from an exported JSON.

Usage:
    python debug_extract.py [export_file]

Defaults to the most recent file in exports/.
"""

import json
import sys
from pathlib import Path

from extract import extract_urls, extract_from_cards, _should_skip_url
from source_type import get_source_type


def main():
    exports_dir = Path(__file__).parent / "exports"

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        jsons = sorted(exports_dir.glob("*.json"))
        if not jsons:
            print("No export files found in exports/")
            return
        path = jsons[-1]

    print(f"Loading: {path}\n")
    data = json.loads(path.read_text(encoding="utf-8"))
    messages = data.get("messages", [])
    print(f"Total messages: {len(messages)}")

    # Count by sender_type
    sender_counts: dict[str, int] = {}
    for m in messages:
        st = m.get("sender_type", "unknown")
        sender_counts[st] = sender_counts.get(st, 0) + 1
    print(f"Sender types: {sender_counts}")

    # Filter out bot messages (same as process() does)
    bot_msgs = [m for m in messages if m.get("sender_type") == "app"]
    messages = [m for m in messages if m.get("sender_type") != "app"]
    if bot_msgs:
        print(f"Filtered out {len(bot_msgs)} bot messages")

    # Count by msg_type (after filtering)
    type_counts: dict[str, int] = {}
    for m in messages:
        mt = m.get("msg_type", "unknown")
        type_counts[mt] = type_counts.get(mt, 0) + 1
    print(f"Message types (after bot filter): {type_counts}\n")

    # --- Card URLs ---
    print("=" * 70)
    print("CARD URLs (from interactive messages)")
    print("=" * 70)
    card_results = extract_from_cards(messages)
    if card_results:
        for i, r in enumerate(card_results, 1):
            src = get_source_type(r["url"])
            print(f"  {i:3}. [{src}] {r['title']}")
            print(f"       {r['url']}")
    else:
        print("  (none)")

    # --- Text URLs ---
    print()
    print("=" * 70)
    print("TEXT URLs (from text messages)")
    print("=" * 70)
    card_url_set = {r["url"] for r in card_results}
    text_urls = extract_urls(messages)
    kept = []
    dupes = []
    for url in text_urls:
        if url in card_url_set:
            dupes.append(url)
        else:
            kept.append(url)

    if kept:
        for i, url in enumerate(kept, 1):
            src = get_source_type(url)
            print(f"  {i:3}. [{src}] {url}")
    else:
        print("  (none)")

    if dupes:
        print(f"\n  ({len(dupes)} text URLs already in cards, skipped)")

    # --- Summary ---
    total = len(card_results) + len(kept)
    print()
    print("=" * 70)
    print(f"SUMMARY: {len(card_results)} card + {len(kept)} text = {total} total URLs")
    print("=" * 70)

    # --- Show all skipped URLs (re-scan without filter) ---
    print()
    print("=" * 70)
    print("SKIPPED URLs (noise domains filtered out)")
    print("=" * 70)
    # Re-extract raw URLs from text messages without filtering
    import re
    _URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")
    skipped = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
                text = parsed.get("text", "") if isinstance(parsed, dict) else content
            except (json.JSONDecodeError, TypeError):
                text = content
        else:
            continue
        for match in _URL_RE.finditer(text):
            url = match.group().rstrip(",.;:!?)>")
            if url and _should_skip_url(url):
                skipped.append(url)

    if skipped:
        for i, url in enumerate(skipped, 1):
            print(f"  {i:3}. {url}")
    else:
        print("  (none)")


if __name__ == "__main__":
    main()
