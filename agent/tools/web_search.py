"""Web 搜索工具 — 使用 Tavily 或 Exa 搜索网络获取最新财经信息。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal

from langchain_core.tools import tool

from ..config import get_config


@tool
def search_web(
    query: str,
    num_results: int = 5,
    search_depth: Literal["basic", "advanced"] = "basic",
    topic: Literal["general", "news", "finance"] = "general",
) -> str:
    """搜索网络获取最新信息，主要用于财经新闻和时事热点。

    ## 何时使用
    - 快速变化的信息：股价、突发新闻、政策变动、市场行情
    - 需要验证当前状态的信息：谁担任某职位、某政策是否生效、某公司最新动态
    - 群聊消息中出现的不熟悉术语、公司、概念
    - 用户明确要求查询最新/当前信息

    ## 何时不要使用
    - 已有的静态事实（历史事件、科学原理、基本定义）
    - 已从群聊消息或 URL 中获取到的信息（避免重复搜索）
    - 通识知识和变化缓慢的信息

    ## 查询技巧
    - 查询词保持 2-6 个词，短而精确
    - 先宽泛搜索再细化（如先搜"A股 行情"再搜"沪深300 跌幅 原因"）
    - 每次查询必须有意义地不同，不要重复相近的查询
    - 搜索中文财经内容时用中文关键词
    - 需要特定日期信息时在查询中包含日期

    ## 搜索后建议
    搜索结果摘要可能不够详细。如果需要某条结果的完整内容，
    请对结果中的 URL 调用 fetch_url_content 获取全文。

    Args:
        query: 搜索关键词，2-6 个词效果最佳。
        num_results: 返回结果数量，简单事实查 3 条，深度研究查 5-8 条。
        search_depth: 搜索深度，"basic" 快速搜索，"advanced" 深度搜索（更慢但更全面）。
        topic: 搜索领域，"general" 通用，"news" 新闻时事，"finance" 财经专题。

    Returns:
        搜索结果摘要，包含标题、链接和内容摘要。
    """
    config = get_config()

    # Priority 1: Use Exa if configured
    exa_key = config.get("EXA_API_KEY", "")
    if exa_key:
        return _search_exa(query, num_results, exa_key, topic)

    # Priority 2: Fallback to Tavily
    tavily_key = config.get("TAVILY_API_KEY", "")
    if tavily_key:
        return _search_tavily(query, num_results, tavily_key, search_depth, topic)

    return "错误：未配置搜索 API Key。请在 .env 中设置 EXA_API_KEY 或 TAVILY_API_KEY。"


def _search_tavily(
    query: str,
    num_results: int,
    api_key: str,
    search_depth: str,
    topic: str,
) -> str:
    """Use Tavily search API."""
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)

        kwargs: dict = {
            "max_results": num_results,
            "search_depth": search_depth,
        }
        # Tavily supports topic filter for news
        if topic == "news":
            kwargs["topic"] = "news"

        results = client.search(query, **kwargs)

        lines = [f"搜索结果（{query}）：", ""]
        for i, result in enumerate(results.get("results", []), 1):
            title = result.get("title", "无标题")
            url = result.get("url", "")
            content = result.get("content", "")[:300]
            published_date = result.get("published_date", "")
            lines.append(f"{i}. **{title}**")
            lines.append(f"   链接：{url}")
            if published_date:
                lines.append(f"   发布时间：{published_date}")
            lines.append(f"   摘要：{content}")
            lines.append("")

        if not results.get("results"):
            lines.append("未找到相关结果，建议换用更短或更宽泛的关键词重试。")

        return "\n".join(lines)
    except Exception as e:
        return f"Tavily 搜索出错：{e}"


def _search_exa(
    query: str,
    num_results: int,
    api_key: str,
    topic: str,
) -> str:
    """Use Exa search API with enhanced content retrieval."""
    try:
        from exa_py import Exa

        exa = Exa(api_key=api_key)

        # 近 7 天日期过滤
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

        kwargs: dict = {
            "type": "auto",
            "num_results": num_results,
            "start_published_date": start_date,
            "contents": {
                "highlights": {"max_characters": 300},
                "text": {"max_characters": 1500},
                "summary": {"query": query},
            },
        }
        # Exa supports category filter
        if topic == "news":
            kwargs["category"] = "news"
        elif topic == "finance":
            kwargs["category"] = "finance"

        result = exa.search(query, **kwargs)

        # Format as human-readable text (consistent with Tavily output)
        lines = [f"搜索结果（{query}）：", ""]
        for i, item in enumerate(result.results, 1):
            title = getattr(item, "title", "无标题")
            url = getattr(item, "url", "")
            published_date = getattr(item, "published_date", "")
            summary = getattr(item, "summary", "")
            text = getattr(item, "text", "")
            highlights = getattr(item, "highlights", [])

            lines.append(f"{i}. **{title}**")
            lines.append(f"   链接：{url}")
            if published_date:
                lines.append(f"   发布时间：{published_date}")
            if summary:
                lines.append(f"   摘要：{summary[:300]}")
            elif text:
                lines.append(f"   内容：{text[:300]}")
            elif highlights:
                lines.append(f"   要点：{highlights[0][:300]}")
            lines.append("")

        if not result.results:
            lines.append("未找到相关结果，建议换用更短或更宽泛的关键词重试。")

        return "\n".join(lines)
    except Exception as e:
        return f"Exa 搜索出错：{e}"
