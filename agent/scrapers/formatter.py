"""Generate daily markdown report from collected articles."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime

from .config import OUTPUT_DIR
from .base import Article


def generate_report(
    articles: list[Article],
    errors: list[str],
) -> str:
    """Generate markdown report and save to output/ directory. Returns the file path."""
    today = datetime.now().strftime("%Y-%m-%d")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Group articles by source
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in articles:
        by_source[a.source].append(a)

    precious = [a for a in articles if a.is_precious_metals]

    lines: list[str] = []

    # --- Header ---
    lines.append(f"# 贵金属新闻日报 {today}")
    lines.append("")
    lines.append(f"> 生成时间: {timestamp}")
    lines.append("")

    # --- Summary ---
    lines.append("## 概览")
    lines.append("")
    lines.append(f"- **总文章数**: {len(articles)}")
    lines.append(f"- **贵金属相关**: {len(precious)}")
    for source, arts in sorted(by_source.items()):
        pm_count = sum(1 for a in arts if a.is_precious_metals)
        lines.append(f"- **{source}**: {len(arts)} 篇 (贵金属 {pm_count} 篇)")
    lines.append("")

    # --- Precious metals headlines table ---
    if precious:
        lines.append("## 贵金属要闻")
        lines.append("")
        lines.append("| 来源 | 标题 | 关键词 |")
        lines.append("|------|------|--------|")
        for a in precious:
            title_link = f"[{_escape_md(a.title)}]({a.url})" if a.url else _escape_md(a.title)
            keywords = ", ".join(a.tags) if a.tags else "-"
            lines.append(f"| {a.source} | {title_link} | {keywords} |")
        lines.append("")

    # --- All articles by source ---
    lines.append("## 全部文章")
    lines.append("")
    for source in sorted(by_source.keys()):
        arts = by_source[source]
        lines.append(f"### {source} ({len(arts)} 篇)")
        lines.append("")
        for a in arts:
            # Title with link
            if a.url:
                lines.append(f"- **[{_escape_md(a.title)}]({a.url})**")
            else:
                lines.append(f"- **{_escape_md(a.title)}**")

            if a.summary:
                lines.append(f"  - {_escape_md(a.summary[:150])}")

            if a.is_precious_metals and a.tags:
                lines.append(f"  - 🏷️ 贵金属关键词: {', '.join(a.tags)}")

            lines.append("")

    # --- Errors ---
    if errors:
        lines.append("## 错误与警告")
        lines.append("")
        for err in errors:
            lines.append(f"```\n{err}\n```")
            lines.append("")

    content = "\n".join(lines)

    # Save to file
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"新闻榜单_{today}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def _escape_md(text: str) -> str:
    """Escape markdown special chars in text for table cells."""
    return text.replace("|", "\\|").replace("\n", " ")
