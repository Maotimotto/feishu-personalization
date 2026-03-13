"""Pipeline — 为达人生成个性化热点榜单（固定流程）。

流程：
1. 加载达人提示词文件（instructions + creator_profile + output_format）
2. 收集新闻素材（爬虫抓取 / Web Search，按达人配置决定）
3a. LLM 生成初版榜单 + 提取搜索关键词
3b. WebSearch 深度搜索背景信息
3c. LLM 根据背景信息优化最终榜单
4. 将生成的 Markdown 内容创建为飞书文档
"""

from __future__ import annotations

import glob
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from typing import Callable

from .config import get_config, get_llm
from .tools.create_feishu_doc import create_feishu_doc_from_markdown
from .tools.web_search import search_web

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─── 达人数据源配置 ─────────────────────────────────────────────────────────
# scrapers: 需要运行的爬虫源名称列表（对应 scrapers/main.py 中的 SOURCE_MAP）
# jin10_breakfast: 是否抓取金十每日早餐全文
# 未在此处列出的达人，走默认的 web search 流程

CREATOR_DATA_SOURCES: dict[str, dict] = {
    "飞哥": {
        "scrapers": ["财联社", "金十", "富途", "东方财富"],
        "jin10_breakfast": True,
    },
    "Trader": {
        "scrapers": ["财联社早报"],
        "jin10_breakfast": True,
    },
}


# ─── Creator prompt loading ──────────────────────────────────────────────────

def find_prompt_file(creator_name: str) -> str | None:
    """Find the prompt file for a creator by name.

    Matches files like '{creator_name}*提示词.txt' in the project root.
    """
    pattern = os.path.join(_PROJECT_ROOT, f"{creator_name}*提示词.txt")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    # Fallback: search all prompt files for a partial name match
    all_prompts = glob.glob(os.path.join(_PROJECT_ROOT, "*提示词.txt"))
    for path in all_prompts:
        basename = os.path.basename(path)
        if creator_name in basename:
            return path
    return None


def list_creators() -> list[str]:
    """List all available creator names from prompt files in project root."""
    pattern = os.path.join(_PROJECT_ROOT, "*提示词.txt")
    creators = []
    for path in glob.glob(pattern):
        basename = os.path.basename(path)
        # Extract creator name: everything before '个性化' or '提示词'
        match = re.match(r'^(.+?)(?:个性化.*)?提示词\.txt$', basename)
        if match:
            creators.append(match.group(1))
    return creators


def load_prompt_template(filepath: str) -> str:
    """Load the prompt template, extracting static parts (instructions + profile + output_format).

    Strips the <headlines> / <today's headlines> section since headlines
    will be fetched dynamically via web search.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Remove the <headlines> or <today's headlines> section
    content = re.sub(
        r"<(?:today's )?headlines>.*?</(?:today's )?headlines>",
        "",
        content,
        flags=re.DOTALL,
    )

    return content.strip()


# ─── Step 1: Generate search queries ─────────────────────────────────────────

def _generate_search_queries(llm, template: str, today: str) -> list[dict]:
    """Ask LLM to generate search queries based on creator profile.

    Returns a list of dicts: [{"query": "...", "topic": "news"}, ...]
    """
    messages = [
        SystemMessage(content=(
            "你是一个搜索关键词生成器。根据下面的达人画像和指示，"
            "生成用于获取今日财经新闻素材的搜索关键词列表。\n\n"
            "要求：\n"
            "- 生成 5-10 组搜索关键词\n"
            "- 覆盖：通用财经早报、达人关注的细分领域、海外市场\n"
            "- 每个关键词 2-6 个词，简短精准\n"
            "- topic 从 general / news / finance 中选择\n\n"
            "严格按以下 JSON 格式输出，不要输出其他内容：\n"
            '[{"query": "关键词", "topic": "news"}, ...]'
        )),
        HumanMessage(content=(
            f"今天是{today}。\n\n"
            f"达人画像与指示：\n{template}"
        )),
    ]

    response = llm.invoke(messages)
    content = response.content.strip()

    # Extract JSON array from response (handle markdown code blocks)
    json_match = re.search(r'\[.*?\]', content, re.DOTALL)
    if json_match:
        try:
            queries = json.loads(json_match.group(0))
            if isinstance(queries, list):
                return queries
        except json.JSONDecodeError:
            pass
        # Greedy fallback: try matching the widest bracket span
        json_match_greedy = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match_greedy:
            try:
                queries = json.loads(json_match_greedy.group(0))
                if isinstance(queries, list):
                    return queries
            except json.JSONDecodeError:
                pass

    try:
        queries = json.loads(content)
        if isinstance(queries, list):
            return queries
    except json.JSONDecodeError:
        pass

    # Fallback: default queries
    return [
        {"query": f"财经早报 {today}", "topic": "news"},
        {"query": "今日财经新闻", "topic": "news"},
        {"query": "A股 市场行情", "topic": "finance"},
        {"query": "美股 市场行情", "topic": "finance"},
        {"query": "宏观经济 政策", "topic": "news"},
    ]


# ─── Step 2: Execute searches ────────────────────────────────────────────────

def _execute_searches(queries: list[dict]) -> str:
    """Execute all search queries and aggregate results into a single text."""
    all_results = []

    for i, q in enumerate(queries, 1):
        query = q.get("query", "")
        topic = q.get("topic", "news")
        num_results = q.get("num_results", 5)

        if not query:
            continue

        print(f"[pipeline]   Search {i}/{len(queries)}: {query}")
        try:
            result = search_web.invoke({
                "query": query,
                "num_results": num_results,
                "topic": topic,
            })
            all_results.append(f"### 搜索「{query}」的结果：\n{result}")
        except Exception as e:
            print(f"[pipeline]   Search failed for '{query}': {e}")
            all_results.append(f"### 搜索「{query}」失败：{e}")

    return "\n\n".join(all_results)


# ─── Step 2b: Collect scraper data (for configured creators) ─────────────

def _collect_scraper_data(source_cfg: dict) -> str:
    """Run scrapers and jin10_breakfast, return aggregated text for LLM."""
    from .scrapers.jin10_breakfast import get_today_breakfast

    sections: list[str] = []

    # ── 金十每日早餐 ──
    if source_cfg.get("jin10_breakfast"):
        print("[pipeline]   Fetching 金十每日早餐...")
        try:
            breakfast = get_today_breakfast()
            if breakfast["content"]:
                sections.append(
                    f"## 金十数据全球财经早餐\n"
                    f"来源：{breakfast['url']}\n\n"
                    f"{breakfast['content']}"
                )
                print("[pipeline]   金十早餐抓取成功")
            else:
                print("[pipeline]   金十早餐：未找到今日内容")
        except Exception as e:
            print(f"[pipeline]   金十早餐抓取失败: {e}")

    # ── 各新闻源爬虫 ──
    scraper_sources = source_cfg.get("scrapers", [])
    if scraper_sources:
        from .scrapers.cls import CLSScraper
        from .scrapers.cls_morning import CLSMorningScraper
        from .scrapers.jin10 import Jin10Scraper
        from .scrapers.futu import FutuScraper
        from .scrapers.eastmoney_news import EastmoneyNewsScraper
        from .scrapers.base import BaseScraper

        source_map: dict[str, type[BaseScraper]] = {
            "财联社": CLSScraper,
            "财联社早报": CLSMorningScraper,
            "金十": Jin10Scraper,
            "富途": FutuScraper,
            "东方财富": EastmoneyNewsScraper,
        }

        def _fetch_one(name: str) -> str | None:
            cls = source_map.get(name)
            if not cls:
                return None
            print(f"[pipeline]   [{name}] 正在抓取...")
            scraper = cls()
            articles, errors = scraper.fetch()
            if errors:
                for err in errors:
                    print(f"[pipeline]   [{name}] 错误: {err[:120]}")
            if not articles:
                return None
            print(f"[pipeline]   [{name}] 获取 {len(articles)} 篇文章")
            lines = [f"## {name} ({len(articles)} 篇)\n"]
            for a in articles:
                lines.append(f"- **{a.title}**")
                if a.summary:
                    lines.append(f"  {a.summary[:200]}")
                if a.url:
                    lines.append(f"  链接：{a.url}")
                lines.append("")
            return "\n".join(lines)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_fetch_one, name): name
                for name in scraper_sources
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    if result:
                        sections.append(result)
                        print(f"[pipeline]   {name} 数据已收集")
                except Exception as e:
                    print(f"[pipeline]   {name} 抓取失败: {e}")

    return "\n\n---\n\n".join(sections)


# ─── Step 3: Generate content ────────────────────────────────────────────────

def _generate_content(llm, template: str, today: str, search_results: str) -> str:
    """Ask LLM to generate the final personalized hotlist from search results."""
    messages = [
        SystemMessage(content=template),
        HumanMessage(content=(
            f"今天是{today}。\n\n"
            f"以下是今日搜索到的新闻素材：\n\n"
            f"{search_results}\n\n"
            f"请根据以上新闻素材和达人画像，生成个性化热点榜单。\n"
            f"按照 output_format 的格式输出 Markdown 内容。"
        )),
    ]

    response = llm.invoke(messages)
    return response.content


# ─── Step 3a: Generate initial content + search queries ─────────────────────

def _generate_initial_content_and_queries(
    llm, template: str, today: str, news_material: str,
) -> tuple[str, list[dict]]:
    """First LLM call: produce an initial hotlist AND search keywords for deeper research.

    Returns:
        (initial_content, search_queries) where search_queries is a list of
        dicts like [{"query": "...", "reason": "..."}, ...].
    """
    messages = [
        SystemMessage(content=template),
        HumanMessage(content=(
            f"今天是{today}。\n\n"
            f"以下是今日搜索到的新闻素材：\n\n"
            f"{news_material}\n\n"
            "请完成以下两项任务：\n\n"
            "【任务一】根据以上新闻素材和达人画像，生成个性化热点榜单初版。"
            "按照 output_format 的格式输出 Markdown 内容。\n\n"
            "【任务二】针对初版榜单中的各个选题，列出需要深度搜索的关键词，"
            "用于后续补充背景资料、关键数据和深度信息。\n\n"
            "请严格按以下格式输出，使用 XML 标签分隔：\n\n"
            "<initial_content>\n"
            "（这里输出初版榜单 Markdown）\n"
            "</initial_content>\n\n"
            "<search_queries>\n"
            '[{"query": "搜索关键词", "reason": "搜索原因"}, ...]\n'
            "</search_queries>"
        )),
    ]

    response = llm.invoke(messages)
    content = response.content

    # Parse initial_content
    initial_match = re.search(
        r"<initial_content>\s*(.*?)\s*</initial_content>", content, re.DOTALL
    )
    initial_content = initial_match.group(1).strip() if initial_match else content

    # Parse search_queries
    queries: list[dict] = []
    queries_match = re.search(
        r"<search_queries>\s*(.*?)\s*</search_queries>", content, re.DOTALL
    )
    if queries_match:
        raw = queries_match.group(1).strip()
        # Try non-greedy first, then greedy as fallback
        for pattern in (r"\[.*?\]", r"\[.*\]"):
            json_match = re.search(pattern, raw, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(0))
                    if isinstance(parsed, list):
                        queries = parsed
                        break
                except json.JSONDecodeError:
                    continue

    return initial_content, queries


# ─── Step 3b: Deep background search ────────────────────────────────────────

def _search_background(queries: list[dict]) -> str:
    """Execute deep web searches for each query and return aggregated background info."""
    if not queries:
        return ""

    sections: list[str] = []
    for i, q in enumerate(queries, 1):
        query_text = q.get("query", "")
        reason = q.get("reason", "")
        if not query_text:
            continue

        print(f"[pipeline]   Background search {i}/{len(queries)}: {query_text}")
        try:
            result = search_web.invoke({
                "query": query_text,
                "num_results": 5,
                "search_depth": "advanced",
                "topic": "news",
            })
            header = f"### 「{query_text}」"
            if reason:
                header += f"（{reason}）"
            sections.append(f"{header}\n{result}")
        except Exception as e:
            print(f"[pipeline]   Background search failed for '{query_text}': {e}")

    return "\n\n".join(sections)


# ─── Step 3c: Refine content with background ────────────────────────────────

def _refine_content(
    llm, template: str, initial_content: str, background_info: str,
) -> str:
    """Second LLM call: refine the initial hotlist using deep background info."""
    messages = [
        SystemMessage(content=(
            "你是一位资深财经内容编辑。你的任务是根据补充的背景资料，优化初版热点榜单。\n\n"
            "优化要求：\n"
            "- 丰富选题的切入角度，增加深度分析维度\n"
            "- 补充关键数据、行业背景、市场影响等细节\n"
            "- 修正初版中可能存在的事实错误\n"
            "- 保持原有的格式和风格不变\n"
            "- 不要凭空添加没有素材支撑的内容\n\n"
            "以下是达人画像供参考：\n" + template
        )),
        HumanMessage(content=(
            "以下是初版榜单：\n\n"
            f"{initial_content}\n\n"
            "---\n\n"
            "以下是深度搜索获取的背景资料：\n\n"
            f"{background_info}\n\n"
            "---\n\n"
            "请根据以上背景资料优化初版榜单，直接输出最终 Markdown 内容。"
        )),
    ]

    response = llm.invoke(messages)
    return response.content


# ─── Pipeline execution ─────────────────────────────────────────────────────

def run_pipeline(
    creator_name: str,
    chat_id: str,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Execute the personalized hot topics pipeline for a creator.

    Fixed pipeline steps:
        1. Load creator prompt
        2. LLM generates search queries based on creator profile
        3. Execute searches and collect results
        4. LLM generates final content from search results
        5. Create Feishu document

    Args:
        creator_name: Name of the creator (e.g., "Trader", "飞哥").
        chat_id: Feishu chat ID to send the result to.
        on_progress: Optional callback invoked with a progress message string
            at each pipeline step.

    Returns:
        The Feishu document URL, or an error message.
    """
    def _progress(msg: str) -> None:
        print(f"[pipeline] {msg}")
        if on_progress:
            on_progress(msg)

    # 1. Load creator prompt
    prompt_file = find_prompt_file(creator_name)
    if not prompt_file:
        error = f"未找到达人 '{creator_name}' 的提示词文件"
        _progress(error)
        return error

    template = load_prompt_template(prompt_file)
    _progress("加载提示词完成")

    today = datetime.now().strftime("%Y年%m月%d日")
    llm = get_llm()

    # 2. Collect news material — scrapers or web search
    source_cfg = CREATOR_DATA_SOURCES.get(creator_name)

    if source_cfg:
        # ── 使用爬虫抓取数据 ──
        _progress("收集新闻素材...")
        news_material = _collect_scraper_data(source_cfg)
    else:
        # ── 默认走 Web Search 流程 ──
        _progress("生成搜索关键词...")
        queries = _generate_search_queries(llm, template, today)
        _progress(f"生成 {len(queries)} 个搜索关键词")

        _progress("执行搜索...")
        news_material = _execute_searches(queries)

    if not news_material.strip():
        return "未获取到任何新闻素材"

    _progress("收集新闻素材完成")

    # 3a. LLM generates initial content + search queries
    _progress("生成初版内容...")
    initial_content, search_queries = _generate_initial_content_and_queries(
        llm, template, today, news_material
    )
    _progress(f"生成初版内容 ({len(initial_content)}字)")

    # 3b. WebSearch deep background search
    if search_queries:
        _progress(f"深度搜索 ({len(search_queries)}个查询)...")
        background_info = _search_background(search_queries)
        _progress(f"深度搜索完成 ({len(search_queries)}个查询)")
    else:
        _progress("无需深度搜索")
        background_info = ""

    # 3c. LLM refines content with background info
    if background_info.strip():
        _progress("优化最终内容...")
        final_content = _refine_content(llm, template, initial_content, background_info)
    else:
        _progress("无背景资料，使用初版内容")
        final_content = initial_content

    if not final_content:
        return "Pipeline 未生成内容"

    _progress(f"优化最终内容 ({len(final_content)}字)")

    # 4. Create Feishu document
    _progress("创建飞书文档...")
    doc_title = f"☀️ {creator_name} 早间热点速览 · {datetime.now().strftime('%Y-%m-%d')}"
    result = create_feishu_doc_from_markdown(doc_title, final_content)

    if "error" in result:
        error_msg = f"创建飞书文档失败: {result['error']}"
        _progress(error_msg)
        return error_msg

    doc_url = result["doc_url"]
    _progress("创建飞书文档完成")

    return doc_url
