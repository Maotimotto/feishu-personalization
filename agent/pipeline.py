"""Pipeline — 直接代码流替代 Agent 模式，按固定顺序串行执行。"""

from __future__ import annotations

import json
import time
from datetime import datetime

from .config import get_config, get_llm
from .tools.fetch_messages import fetch_chat_messages
from .tools.extract_urls import extract_urls
from .tools.fetch_url_content import fetch_url_content
from .tools.classify_titles import classify_titles
from .tools.create_spreadsheet import create_feishu_spreadsheet
from .tools.send_message import send_feishu_message
from .tools.export_data import export_data
from .tools.web_search import search_web

# ---------------------------------------------------------------------------
# Step 5: 生成搜索词条 — 每个热词分组单独调 LLM
# ---------------------------------------------------------------------------

_SEARCH_TERMS_PROMPT = """你是一个专业的财经分析师。给定一个热词和该热词下的所有选题标题，分析这些选题涉及的核心话题，按多个维度生成需要进行 web search 的词条列表。

今天是 {today}。

## 搜索维度（必须覆盖以下 4 个方面）
1. **事件本身（event）**：这件事是什么？最新进展？
2. **背景原因（background）**：为什么发生？历史脉络？
3. **涉及标的（assets）**：涉及的公司、股票、行业、资产品种？
4. **市场影响（impact）**：对市场的影响是什么？各方反应？

## 搜索策略
- 每个词条 1-6 个词，短而精确
- 3-5 个词条，确保覆盖不同维度
- 每个词条必须有意义地不同，不要重复相近内容
- 搜索中文财经内容用中文关键词
- 需要特定日期信息时在词条中包含日期

## 输出要求
返回一个 JSON 数组，每个元素包含 aspect 和 query 两个字段。
- aspect: 维度标签，取值为 event / background / assets / impact
- query: 搜索词条字符串
- 3-5 个词条
- 只返回 JSON 数组，不要返回任何其他内容

## 输出示例
[
  {{"aspect": "event", "query": "美联储3月议息会议 结果"}},
  {{"aspect": "background", "query": "美联储 降息周期 历史"}},
  {{"aspect": "assets", "query": "美联储降息 受益板块 股票"}},
  {{"aspect": "impact", "query": "美联储降息 市场影响 美元 黄金"}}
]"""

# ---------------------------------------------------------------------------
# Step 8: 优化热词 — 结合搜索结果精炼
# ---------------------------------------------------------------------------

_REFINE_PROMPT = """你是一个专业的财经编辑。根据以下信息，为这组标题生成一个更精准的热词。

今天是 {today}。

## 原始热词
{keyword}

## 该分类下的标题
{titles}

## 搜索获取的实时背景信息
{search_results}

## 要求
1. 生成一个优化后的热词，替代原始热词
2. 中文名词短语，不是句子
3. 不超过 10 个字符
4. 更具体（如"美联储降息"优于"美联储"）
5. 有概括性（涵盖组内所有标题的核心主题）
6. 准确（结合实时信息反映最新事实）
7. 只返回热词文本，不要任何其他内容"""

# ---------------------------------------------------------------------------
# Step 7: 深度分析 — 基于搜索结果进行结构化事件分析
# ---------------------------------------------------------------------------

_DEEP_ANALYSIS_PROMPT = """你是一个专业的财经分析师。根据热词、相关选题标题和搜索获取的信息，对这个热点事件进行深度分析。

今天是 {today}。

## 热词
{keyword}

## 相关选题标题
{titles}

## 搜索结果
{search_results}

## 输出要求
返回一个 JSON 对象，包含以下字段：
- event_summary: 一句话概述事件（30字以内）
- background: 事件背景和来龙去脉（2-3句话）
- timeline: 关键时间节点（如：3月8日xxx，3月9日xxx）
- affected_assets: 涉及的资产/标的列表（数组，如 ["美元指数", "黄金", "美债"]）
- market_impact: 对市场的影响分析（2-3句话）
- key_entities: 关键实体列表（数组，如 ["美联储", "鲍威尔", "FOMC"]）

只返回 JSON 对象，不要返回任何其他内容。"""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _generate_search_terms(keyword: str, group_titles: list[str], llm) -> list[dict]:
    """Step 5: 对单个热词分组，调 LLM 生成带维度标注的搜索词条列表。

    返回格式: [{"aspect": "event", "query": "..."}, ...]
    """
    today = datetime.now().strftime("%Y-%m-%d")
    title_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(group_titles))

    try:
        resp = llm.invoke([
            {"role": "system", "content": _SEARCH_TERMS_PROMPT.format(today=today)},
            {"role": "user", "content": f"热词：{keyword}\n选题标题：\n{title_list}"},
        ])
        terms_text = _strip_code_fences(resp.content)
        terms = json.loads(terms_text)
        if isinstance(terms, list):
            # 兼容新旧格式
            result = []
            for t in terms[:5]:
                if isinstance(t, dict) and "query" in t:
                    result.append({"aspect": t.get("aspect", "event"), "query": str(t["query"])})
                elif isinstance(t, str):
                    result.append({"aspect": "event", "query": t})
            return result if result else [{"aspect": "event", "query": keyword}]
    except Exception as e:
        print(f"[pipeline] 生成搜索词条失败 '{keyword}': {e}")

    # Fallback: 用热词本身作为搜索词
    return [{"aspect": "event", "query": keyword}]


def _execute_web_search(terms: list[dict], config: dict) -> list[dict]:
    """Step 6: 按序对词条列表执行 web search，返回结构化搜索结果列表。

    返回格式: [{"aspect": "event", "query": "...", "results": [...]}, ...]
    """
    all_results = []

    for term in terms:
        query = term["query"]
        aspect = term.get("aspect", "event")
        print(f"[pipeline] 搜索 [{aspect}]: {query}")
        raw = search_web.invoke({"query": query, "num_results": 5, "topic": "news"})

        # 解析结构化结果（Exa 返回 JSON）或纯文本（Tavily 回退）
        try:
            parsed = json.loads(raw)
            results = parsed.get("results", [])
        except (json.JSONDecodeError, TypeError):
            # Tavily 返回纯文本，保持兼容
            results = [{"text": raw}] if raw and "未找到" not in raw and "错误" not in raw else []

        if results:
            all_results.append({"aspect": aspect, "query": query, "results": results})

    return all_results


def _format_search_for_llm(search_data: list[dict]) -> str:
    """将结构化搜索结果格式化为 LLM 可读的文本。"""
    if not search_data:
        return "（未获取到搜索结果）"

    sections = []
    for item in search_data:
        query = item.get("query", "")
        aspect = item.get("aspect", "")
        results = item.get("results", [])
        lines = [f"【{aspect}: {query}】"]
        for r in results:
            title = r.get("title", "")
            summary = r.get("summary", "")
            text = r.get("text", "")
            highlight = r.get("highlight", "")
            if title:
                lines.append(f"  标题: {title}")
            # 优先使用 summary，其次 text，最后 highlight
            content = summary or text or highlight
            if content:
                lines.append(f"  内容: {content[:800]}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _deep_analyze(
    keyword: str,
    group_titles: list[str],
    search_data: list[dict],
    llm,
) -> str:
    """Step 7: 基于搜索结果进行深度分析，返回格式化的事件分析文本。"""
    today = datetime.now().strftime("%Y-%m-%d")
    title_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(group_titles))
    search_text = _format_search_for_llm(search_data)

    try:
        resp = llm.invoke([
            {
                "role": "system",
                "content": _DEEP_ANALYSIS_PROMPT.format(
                    today=today,
                    keyword=keyword,
                    titles=title_list,
                    search_results=search_text,
                ),
            },
            {"role": "user", "content": "请对该热点事件进行深度分析。"},
        ])
        analysis_text = _strip_code_fences(resp.content)
        analysis = json.loads(analysis_text)

        # 格式化为可读文本
        parts = []
        if analysis.get("event_summary"):
            parts.append(f"【概述】{analysis['event_summary']}")
        if analysis.get("background"):
            parts.append(f"【背景】{analysis['background']}")
        if analysis.get("timeline"):
            parts.append(f"【时间线】{analysis['timeline']}")
        if analysis.get("affected_assets"):
            assets = analysis["affected_assets"]
            if isinstance(assets, list):
                parts.append(f"【涉及标的】{'、'.join(assets)}")
            else:
                parts.append(f"【涉及标的】{assets}")
        if analysis.get("market_impact"):
            parts.append(f"【市场影响】{analysis['market_impact']}")
        if analysis.get("key_entities"):
            entities = analysis["key_entities"]
            if isinstance(entities, list):
                parts.append(f"【关键实体】{'、'.join(entities)}")
            else:
                parts.append(f"【关键实体】{entities}")

        return "\n".join(parts) if parts else "（分析生成失败）"
    except Exception as e:
        print(f"[pipeline] 深度分析失败 '{keyword}': {e}")
        # Fallback: 返回搜索结果的纯文本摘要
        return _format_search_for_llm(search_data)[:500] if search_data else "（分析生成失败）"


def _refine_single_keyword(
    keyword: str,
    group_titles: list[str],
    search_data: list[dict],
    llm,
) -> str:
    """Step 8: 结合搜索结果优化单个热词。"""
    today = datetime.now().strftime("%Y-%m-%d")
    title_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(group_titles))
    search_results_text = _format_search_for_llm(search_data)

    try:
        resp = llm.invoke([
            {
                "role": "system",
                "content": _REFINE_PROMPT.format(
                    today=today,
                    keyword=keyword,
                    titles=title_list,
                    search_results=search_results_text,
                ),
            },
            {"role": "user", "content": "请生成优化后的热词。"},
        ])
        refined = resp.content.strip().strip("\"'")
        if refined and len(refined) <= 10:
            return refined
    except Exception as e:
        print(f"[pipeline] 优化热词失败 '{keyword}': {e}")

    return keyword


# ---------------------------------------------------------------------------
# 主流水线
# ---------------------------------------------------------------------------


def run_pipeline(chat_id: str, duration: str = "1d") -> str:
    """直接代码流执行完整的链接汇总流水线。

    Step 1: fetch_chat_messages
    Step 2: extract_urls
    Step 3: fetch_url_content
    Step 4: classify_titles
    Step 5: generate_search_terms (per group, LLM, multi-dimension)
    Step 6: web_search (per term, enhanced with text/summary)
    Step 7: deep_analyze (per group, LLM structured analysis) ★ NEW
    Step 8: refine_keywords (per group, LLM + search results)
    Step 9: create_spreadsheet + send_message + export_data

    Args:
        chat_id: 飞书群聊 ID
        duration: 时间范围，如 "2h", "1d"

    Returns:
        最终结果摘要文本
    """
    pipeline_start = time.time()
    config = get_config()

    # ── Step 1: 获取消息 ──
    print(f"\n{'='*60}")
    print(f"[pipeline] Step 1/9: 获取群聊消息 (chat_id={chat_id}, duration={duration})")
    print(f"{'='*60}")
    step1_start = time.time()
    step1_result = fetch_chat_messages.invoke({"chat_id": chat_id, "duration": duration})
    step1_data = json.loads(step1_result)
    msg_count = step1_data.get("message_count", 0)
    print(f"[pipeline] Step 1 完成: {msg_count} 条消息 ({time.time()-step1_start:.1f}s)")

    if msg_count == 0:
        return f"过去 {duration} 内没有群聊消息。"

    # ── Step 2: 提取链接 ──
    print(f"\n{'='*60}")
    print(f"[pipeline] Step 2/9: 提取链接")
    print(f"{'='*60}")
    step2_start = time.time()
    step2_result = extract_urls.invoke({"messages_json": step1_result})
    step2_data = json.loads(step2_result)
    url_count = step2_data.get("url_count", 0)
    print(f"[pipeline] Step 2 完成: {url_count} 个链接 ({time.time()-step2_start:.1f}s)")

    if url_count == 0:
        return f"过去 {duration} 内群聊中没有分享链接。"

    # ── Step 3: 获取链接内容 ──
    print(f"\n{'='*60}")
    print(f"[pipeline] Step 3/9: 获取链接标题和内容")
    print(f"{'='*60}")
    step3_start = time.time()
    step3_result = fetch_url_content.invoke({"urls_json": step2_result})
    step3_data = json.loads(step3_result)
    results_list = step3_data.get("results", [])
    print(f"[pipeline] Step 3 完成: {len(results_list)} 个链接内容 ({time.time()-step3_start:.1f}s)")

    # ── Step 4: 热词分类 ──
    print(f"\n{'='*60}")
    print(f"[pipeline] Step 4/9: LLM 热词分类")
    print(f"{'='*60}")
    step4_start = time.time()
    step4_result = classify_titles.invoke({"titles_json": step3_result})
    step4_data = json.loads(step4_result)
    groups = step4_data.get("groups", {})
    titles = step4_data.get("titles", [])
    print(f"[pipeline] Step 4 完成: {len(groups)} 个热词分组 ({time.time()-step4_start:.1f}s)")

    # ── Steps 5-8: 搜索 + 深度分析 + 优化热词 ──
    print(f"\n{'='*60}")
    print(f"[pipeline] Steps 5-8/9: 搜索词条生成 + Web Search + 深度分析 + 热词优化")
    print(f"{'='*60}")
    step5_start = time.time()

    llm = get_llm()
    refined_groups: dict[str, list[int]] = {}
    analysis_map: dict[str, str] = {}  # keyword → 事件分析文本

    for keyword, indices in groups.items():
        # 跳过特殊分组
        if keyword in ("其他", "未分类"):
            refined_groups[keyword] = indices
            analysis_map[keyword] = ""
            continue

        group_titles = [titles[i] for i in indices if 0 <= i < len(titles)]
        if not group_titles:
            refined_groups[keyword] = indices
            analysis_map[keyword] = ""
            continue

        print(f"\n--- 热词: {keyword} ({len(group_titles)} 个选题) ---")

        # Step 5: 生成搜索词条（多维度）
        terms = _generate_search_terms(keyword, group_titles, llm)
        print(f"[pipeline] 生成搜索词条: {[t['query'] for t in terms]}")

        # Step 6: 执行 web search（增强版）
        search_data = _execute_web_search(terms, config)
        result_count = sum(len(s.get("results", [])) for s in search_data)
        print(f"[pipeline] 搜索完成: {result_count} 条结果")

        # Step 7: 深度分析
        analysis_text = _deep_analyze(keyword, group_titles, search_data, llm)
        print(f"[pipeline] 深度分析完成: {len(analysis_text)} 字")

        # Step 8: 优化热词
        refined = _refine_single_keyword(keyword, group_titles, search_data, llm)

        # 检查重复
        if refined != keyword and refined not in refined_groups:
            print(f"[pipeline] 热词优化: '{keyword}' -> '{refined}'")
            refined_groups[refined] = indices
            analysis_map[refined] = analysis_text
        else:
            if refined != keyword:
                print(f"[pipeline] 热词保留: '{keyword}' (优化结果 '{refined}' 重复)")
            refined_groups[keyword] = indices
            analysis_map[keyword] = analysis_text

    print(f"\n[pipeline] Steps 5-8 完成 ({time.time()-step5_start:.1f}s)")

    # ── Step 9: 创建表格 + 发送消息 + 导出数据 ──
    print(f"\n{'='*60}")
    print(f"[pipeline] Step 9/9: 创建飞书表格 + 发送消息 + 导出数据")
    print(f"{'='*60}")
    step9_start = time.time()

    # 合并数据：refined groups + results + analysis_map
    merged_data = {
        "group_count": len(refined_groups),
        "title_count": len(titles),
        "groups": refined_groups,
        "titles": titles,
        "results": results_list,
        "analysis_map": analysis_map,
    }
    merged_json = json.dumps(merged_data, ensure_ascii=False)

    # 9a: 创建飞书表格
    sheet_result = create_feishu_spreadsheet.invoke({
        "classified_json": merged_json,
    })
    sheet_data = json.loads(sheet_result)
    sheet_url = sheet_data.get("sheet_url", "")
    print(f"[pipeline] 表格创建完成: {sheet_url}")

    # 9b: 发送消息到群聊
    if sheet_url:
        today = datetime.now().strftime("%Y-%m-%d")
        msg_text = f"{today} 热点事件汇总\n{sheet_url}"
        send_feishu_message.invoke({
            "chat_id": chat_id,
            "text": msg_text,
        })
        print(f"[pipeline] 消息已发送到群聊")

    # 9c: 导出数据
    export_result = export_data.invoke({
        "data_json": merged_json,
        "chat_id": chat_id,
        "export_format": "both",
    })
    export_data_result = json.loads(export_result)
    export_files = export_data_result.get("files", [])
    print(f"[pipeline] 数据已导出: {[f.get('path', '') for f in export_files]}")

    total_time = time.time() - pipeline_start
    print(f"\n{'='*60}")
    print(f"[pipeline] 全部完成! 总耗时 {total_time:.1f}s")
    print(f"{'='*60}")

    summary = (
        f"汇总完成！\n"
        f"- 消息: {msg_count} 条\n"
        f"- 链接: {url_count} 个\n"
        f"- 热词分组: {len(refined_groups)} 个\n"
        f"- 表格: {sheet_url}\n"
        f"- 耗时: {total_time:.0f}s"
    )
    return summary
