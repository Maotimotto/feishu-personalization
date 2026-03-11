"""Tool: 热词优化 — LLM 分析 + web search + LLM 精炼。"""

from __future__ import annotations

import json
from datetime import datetime

from langchain_core.tools import tool

from ..config import get_config, get_llm

_ANALYZE_PROMPT = """你是一个专业的财经分析师。给定一个热词分类和该分类下的所有标题，分析这些标题涉及的核心话题，生成 2-5 个搜索查询，用于深入了解相关背景。

今天是 {today}。

## 分析要点
- 事件背景：标题讨论的事件来龙去脉
- 时间节点：关键时间点和最新进展
- 专业术语：需要了解的概念或术语
- 关键实体：重要人物、机构、公司、资产
- 市场影响：对市场或行业的影响

## 输出要求
返回一个 JSON 数组，每个元素是一个搜索查询字符串。
- 每个查询 2-6 个中文词，简短精确
- 2-5 个查询，覆盖不同角度
- 只返回 JSON 数组，不要返回任何其他内容"""

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


@tool
def refine_keywords(classified_json: str) -> str:
    """基于 web search 深度优化 classify_titles 产生的热词。

    对每个热词分类：
    1. LLM 分析该组标题，生成 2-5 个针对性搜索查询（背景、时间、术语等）
    2. 执行 web search 获取实时信息
    3. LLM 结合搜索结果和标题，优化热词使其更具体、更准确

    ## 何时使用
    - 在 classify_titles 之后、create_feishu_spreadsheet 之前调用
    - 这是链接汇总流程的第五步（classify_titles → refine_keywords）

    ## 输入
    classify_titles 的完整 JSON 输出。

    ## 输出
    与 classify_titles 格式完全一致的 JSON，仅热词名称被优化。

    Args:
        classified_json: classify_titles 的 JSON 输出。

    Returns:
        JSON 字符串，热词已优化，格式与输入一致。
    """
    data = json.loads(classified_json)
    groups = data.get("groups", {})
    titles = data.get("titles", [])
    today = datetime.now().strftime("%Y-%m-%d")

    if not groups or not titles:
        return classified_json

    llm = get_llm()
    config = get_config()

    refined_groups = {}

    for keyword, indices in groups.items():
        # Skip special groups
        if keyword in ("其他", "未分类"):
            refined_groups[keyword] = indices
            continue

        group_titles = [titles[i] for i in indices if 0 <= i < len(titles)]
        if not group_titles:
            refined_groups[keyword] = indices
            continue

        title_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(group_titles))

        try:
            # --- Step A: LLM generates search queries ---
            analyze_resp = llm.invoke([
                {"role": "system", "content": _ANALYZE_PROMPT.format(today=today)},
                {"role": "user", "content": f"热词：{keyword}\n标题：\n{title_list}"},
            ])

            queries_text = _strip_code_fences(analyze_resp.content)
            queries = json.loads(queries_text)
            if not isinstance(queries, list):
                queries = [keyword]

            # --- Step B: Web search ---
            search_parts = []
            for q in queries[:5]:
                result = _do_search(q, config)
                if result:
                    search_parts.append(f"【{q}】\n{result}")

            all_search = "\n\n".join(search_parts) if search_parts else "（未获取到搜索结果）"

            # --- Step C: LLM refines keyword ---
            refine_resp = llm.invoke([
                {
                    "role": "system",
                    "content": _REFINE_PROMPT.format(
                        today=today,
                        keyword=keyword,
                        titles=title_list,
                        search_results=all_search,
                    ),
                },
                {"role": "user", "content": "请生成优化后的热词。"},
            ])

            refined = refine_resp.content.strip().strip("\"'")

            if refined and len(refined) <= 10 and refined not in refined_groups:
                refined_groups[refined] = indices
                print(f"[refine] '{keyword}' -> '{refined}' ({len(queries)} queries, {len(search_parts)} results)")
            else:
                refined_groups[keyword] = indices
                print(f"[refine] '{keyword}' kept (refined='{refined}' invalid or duplicate)")

        except Exception as e:
            print(f"[refine] Failed for '{keyword}': {e}")
            refined_groups[keyword] = indices

    output = {
        "group_count": len(refined_groups),
        "title_count": len(titles),
        "groups": refined_groups,
        "titles": titles,
    }
    return json.dumps(output, ensure_ascii=False)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _do_search(query: str, config: dict) -> str:
    exa_key = config.get("EXA_API_KEY", "")
    if exa_key:
        return _search_exa(query, exa_key)
    tavily_key = config.get("TAVILY_API_KEY", "")
    if tavily_key:
        return _search_tavily(query, tavily_key)
    return ""


def _search_exa(query: str, api_key: str) -> str:
    try:
        from exa_py import Exa

        exa = Exa(api_key=api_key)
        result = exa.search(
            query,
            type="auto",
            num_results=3,
            category="news",
            contents={"highlights": {"max_characters": 300}},
        )
        lines = []
        for item in result.results:
            title = getattr(item, "title", "")
            highlights = getattr(item, "highlights", [])
            summary = highlights[0][:200] if highlights else ""
            lines.append(f"- {title}: {summary}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[refine] Exa search error for '{query}': {e}")
        return ""


def _search_tavily(query: str, api_key: str) -> str:
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        results = client.search(query, max_results=3, topic="news")
        lines = []
        for r in results.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")[:200]
            lines.append(f"- {title}: {content}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[refine] Tavily search error for '{query}': {e}")
        return ""
