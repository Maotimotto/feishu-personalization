"""Tool: LLM 驱动的标题热词分类。"""

from __future__ import annotations

import json
from datetime import datetime

from langchain_core.tools import tool

from ..config import get_llm

_SYSTEM_PROMPT = """You are a Chinese financial news classifier. Your sole function is to group a numbered list of article/video titles (财经选题标题) by semantic similarity and label each group with a short Chinese keyword (热词).

Today's date is {today}.

<input_description>
You will receive a numbered list of Chinese financial content titles. Sources include 华尔街见闻、金十数据、富途牛牛、微信公众号、抖音短视频 etc. Some titles may be malformed or meaningless (e.g. "抖音 WVjSD9PcTtI" for videos without a real title).
</input_description>

<rules>
1. Group titles that refer to the same event, same theme, or same asset/entity. Prefer merging over splitting.
2. Produce exactly 6–10 groups. If your initial pass yields more than 10, merge the closest groups. If fewer than 6, check whether any large group can be meaningfully split.
3. Titles that are meaningless or unrelated to all other titles go into a single group with keyword "其他".
4. Every title index must appear in exactly one group. No omissions, no duplicates.
5. Reference titles by their 1-based index number only. Never copy title text into the output.
</rules>

<keyword_format>
Each group's keyword (热词) must be:
- In Chinese
- A noun phrase, not a sentence (e.g. "美联储降息" not "美联储宣布降息了")
- At most 10 characters
</keyword_format>

<output_format>
Return a single JSON object. No markdown fences, no explanation, no text before or after the JSON.

Schema:
{{"groups":[{{"keyword":"string","indices":[int]}}]}}
</output_format>

<example>
Input:
1. 美联储维持利率不变 市场等待鲍威尔讲话
2. 英伟达Q3营收超预期 数据中心业务暴增
3. 鲍威尔释放鸽派信号 美元承压
4. 抖音 abc123
5. 英伟达股价盘后大涨8%
6. 黄金突破2700美元创历史新高
7. 金价飙升背后：央行购金潮持续

Output:
{{"groups":[{{"keyword":"美联储议息","indices":[1,3]}},{{"keyword":"英伟达财报","indices":[2,5]}},{{"keyword":"黄金创新高","indices":[6,7]}},{{"keyword":"其他","indices":[4]}}]}}
</example>"""


@tool
def classify_titles(titles_json: str) -> str:
    """使用 LLM 将标题按语义相似度分组，生成 6-10 个热词标签。

    调用独立的 LLM 对标题列表做聚类分析，每组用一个不超过 10 个字符的
    中文名词短语作为热词关键字（如"美联储降息"、"英伟达财报"）。

    ## 何时使用
    - 获取到链接标题后，需要按主题分组以生成分类汇总时
    - 这是链接汇总流程的第四步（fetch_url_content → classify_titles）

    ## 何时不用
    - 标题数量少于 3 个时，分类意义不大，可直接展示
    - 用户只需要原始链接列表不需要分类时

    ## 输入来源
    直接接收 fetch_url_content 的完整 JSON 输出（包含 results 字段，从中提取 title）。
    也可接收纯标题字符串列表 JSON。

    ## 输出去向
    输出包含 groups（热词→索引映射）和 titles 列表。
    重要：输出中不包含 URL 和 source_type 信息。创建飞书表格时，需要将本工具的
    输出与 fetch_url_content 的 results 字段合并，才能得到完整的分类汇总数据。
    合并方式：将 fetch_url_content 的 results 列表作为 "results" 字段添加到本工具输出中。

    ## 注意
    - 内置 3 次重试机制，LLM 调用失败时会自动重试
    - 无法分类的标题会归入"其他"组
    - 空标题或无意义标题（如"抖音 abc123"）也会被妥善处理

    Args:
        titles_json: fetch_url_content 的完整 JSON 输出，或纯标题列表 JSON。

    Returns:
        JSON 字符串，包含 group_count、title_count、groups（热词→索引列表）和 titles 列表。
    """
    data = json.loads(titles_json)

    # Accept either a results list or a plain list of strings
    if isinstance(data, dict):
        items = data.get("results", [])
        titles = []
        for item in items:
            title = item.get("title", "")
            if title:
                titles.append(title)
            else:
                src = item.get("source_type", "")
                url = item.get("url", "")
                short = url.rstrip("/").rsplit("/", 1)[-1][:12] if url else ""
                titles.append(f"{src} {short}".strip() if src else url)
    elif isinstance(data, list):
        titles = [str(t) for t in data]
    else:
        return json.dumps({"error": "Invalid input"}, ensure_ascii=False)

    if not titles:
        return json.dumps({"groups": {}}, ensure_ascii=False)

    llm = get_llm()

    title_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_content = f"<titles>\n{title_list}\n</titles>\n\nBased on the titles above, classify and return JSON."
    system_prompt = _SYSTEM_PROMPT.format(today=datetime.now().strftime("%Y-%m-%d"))

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = llm.invoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ])

            content = response.content.strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)
            groups = result.get("groups", [])

            mapping: dict[str, list[int]] = {}
            for g in groups:
                keyword = g.get("keyword", "未分类")
                indices = g.get("indices", [])
                if indices:
                    mapping[keyword] = [
                        int(i) - 1
                        for i in indices
                        if 0 < int(i) <= len(titles)
                    ]

            # Ensure all titles are accounted for
            classified = {idx for idxs in mapping.values() for idx in idxs}
            missing = [i for i in range(len(titles)) if i not in classified]
            if missing:
                mapping.setdefault("其他", []).extend(missing)

            output = {
                "group_count": len(mapping),
                "title_count": len(titles),
                "groups": mapping,
                "titles": titles,
            }
            return json.dumps(output, ensure_ascii=False)

        except Exception as e:
            if attempt < max_retries:
                import time
                time.sleep(2 * attempt)
            else:
                mapping = {"未分类": list(range(len(titles)))}
                output = {
                    "group_count": 1,
                    "title_count": len(titles),
                    "groups": mapping,
                    "titles": titles,
                    "error": str(e),
                }
                return json.dumps(output, ensure_ascii=False)
