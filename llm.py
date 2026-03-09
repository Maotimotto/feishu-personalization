"""LLM-powered title classification — group titles into hot-word clusters."""

from __future__ import annotations

import json
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("LLM_API_KEY", "")
_BASE_URL = os.getenv("LLM_BASE_URL", "")
_MODEL = os.getenv("LLM_MODEL", "gemini-3-flash-preview")
_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16384"))
_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

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


def classify_titles(titles: list[str]) -> dict[str, list[int]]:
    """Classify titles into hot-word groups using LLM.

    Args:
        titles: List of title strings to classify.

    Returns:
        Dict mapping hot-word → list of 0-based indices into *titles*.
        On failure, returns a single group with all indices.
    """
    if not titles:
        return {}

    if not _API_KEY or not _BASE_URL:
        print("[llm] LLM_API_KEY or LLM_BASE_URL not configured, skipping classification")
        return {"未分类": list(range(len(titles)))}

    from datetime import datetime as _dt

    title_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_content = f"<titles>\n{title_list}\n</titles>\n\nBased on the titles above, classify and return JSON."

    system_prompt = _SYSTEM_PROMPT.format(today=_dt.now().strftime("%Y-%m-%d"))

    payload = {
        "model": _MODEL,
        "temperature": _TEMPERATURE,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            print(f"[llm] Calling LLM (attempt {attempt}/{_MAX_RETRIES})...")
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    _BASE_URL,
                    headers={
                        "Authorization": f"Bearer {_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()

            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # Strip markdown code fences if present
            content = content.strip()
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

            # Verify all titles are accounted for
            classified = {idx for idxs in mapping.values() for idx in idxs}
            missing = [i for i in range(len(titles)) if i not in classified]
            if missing:
                mapping.setdefault("其他", []).extend(missing)

            print(f"[llm] Classified {len(titles)} titles into {len(mapping)} groups")
            return mapping

        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"[llm] Attempt {attempt} failed: {e}")
            if attempt < _MAX_RETRIES:
                time.sleep(2 * attempt)

    # All retries exhausted
    print("[llm] All retries failed, returning uncategorized")
    return {"未分类": list(range(len(titles)))}
