"""Tool: 创建飞书电子表格。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import httpx
from langchain_core.tools import tool

from ..config import get_config
from ..feishu import api_headers, set_org_editable

_API = "https://open.feishu.cn/open-apis"


@tool
def create_feishu_spreadsheet(classified_json: str, title: str = "") -> str:
    """创建飞书电子表格，按热词分类展示链接汇总。

    表头为：热词 | 选题 | 来源链接 | 来源类型 | 文案。
    自动设置组织可编辑权限，以日期命名工作表。

    ## 何时使用
    - 完成热词分类后，需要创建可视化的链接汇总表时
    - 这是链接汇总流程的第五步（classify_titles → create_feishu_spreadsheet）
    - 这通常是整个汇总流程的主要交付物

    ## 何时不用
    - 用户只需要消息文档 → 应使用 create_feishu_doc
    - 用户只需要本地文件 → 应使用 export_data

    ## 输入要求（重要）
    输入 JSON 必须同时包含以下字段才能生成完整表格：
    - groups：热词→索引映射（来自 classify_titles 输出）
    - titles：标题列表（来自 classify_titles 输出）
    - results：URL 详情列表（来自 fetch_url_content 输出）

    因此，在调用本工具前，需要将 classify_titles 的输出与 fetch_url_content 的
    results 合并。具体做法：将 fetch_url_content 输出中的 results 列表，
    作为 "results" 字段添加到 classify_titles 的输出 JSON 中。

    ## 输出去向
    返回飞书表格 URL，可通过 send_feishu_message 发送到群聊。

    Args:
        classified_json: 合并后的 JSON，需包含 groups、titles 和 results 字段。
        title: 表格标题。默认为 "{日期} 热点事件"。

    Returns:
        JSON 字符串，包含 sheet_url、token、row_count 和 group_count。
    """
    data = json.loads(classified_json)
    groups = data.get("groups", {})
    titles = data.get("titles", [])
    results = data.get("results", [])
    search_results_map = data.get("search_results_map", {})
    analysis_map = data.get("analysis_map", {})

    config = get_config()
    today = datetime.now().strftime("%Y-%m-%d")
    if not title:
        title = f"{today} 热点事件"

    # Build rows
    rows: list[list[str]] = [["热词", "选题", "来源链接", "来源类型", "文案", "事件分析"]]
    for keyword, indices in groups.items():
        # 优先使用 analysis_map（深度分析），回退到 search_results_map
        analysis_text = analysis_map.get(keyword, "") or search_results_map.get(keyword, "")
        first = True
        for idx in indices:
            display_title = titles[idx] if idx < len(titles) else ""
            url = ""
            source_type = ""
            content = ""
            if results and idx < len(results):
                url = results[idx].get("url", "")
                source_type = results[idx].get("source_type", "")
                content = results[idx].get("content", "")
            rows.append([
                keyword if first else "",
                display_title,
                url,
                source_type,
                content,
                analysis_text if first else "",
            ])
            first = False

    # 1. Create spreadsheet
    resp = httpx.post(
        f"{_API}/sheets/v3/spreadsheets",
        headers=api_headers(),
        json={"title": title},
        timeout=15,
    )
    resp_data = resp.json()
    if resp_data["code"] != 0:
        return json.dumps(
            {"error": f"Create spreadsheet failed: code={resp_data['code']} msg={resp_data['msg']}"},
            ensure_ascii=False,
        )
    token = resp_data["data"]["spreadsheet"]["spreadsheet_token"]

    # 2. Set org-editable permission
    set_org_editable(token, "sheet")

    # 3. Query default sheet ID
    resp = httpx.get(
        f"{_API}/sheets/v3/spreadsheets/{token}/sheets/query",
        headers=api_headers(),
        timeout=15,
    )
    resp_data = resp.json()
    if resp_data["code"] != 0:
        return json.dumps(
            {"error": f"Query sheets failed: code={resp_data['code']} msg={resp_data['msg']}"},
            ensure_ascii=False,
        )
    sheet_id = resp_data["data"]["sheets"][0]["sheet_id"]

    # 4. Rename sheet
    today_short = datetime.now().strftime("%Y%m%d")
    httpx.post(
        f"{_API}/sheets/v2/spreadsheets/{token}/sheets_batch_update",
        headers=api_headers(),
        json={
            "requests": [{
                "updateSheet": {
                    "properties": {
                        "sheetId": sheet_id,
                        "title": today_short,
                    }
                }
            }]
        },
        timeout=15,
    )

    # 5. Write data
    row_count = len(rows)
    range_str = f"{sheet_id}!A1:F{row_count}"

    resp = httpx.put(
        f"{_API}/sheets/v2/spreadsheets/{token}/values",
        headers=api_headers(),
        json={"valueRange": {"range": range_str, "values": rows}},
        timeout=30,
    )
    resp_data = resp.json()
    if resp_data["code"] != 0:
        return json.dumps(
            {"error": f"Write values failed: code={resp_data['code']} msg={resp_data['msg']}"},
            ensure_ascii=False,
        )

    domain = config["FEISHU_DOMAIN"]
    sheet_url = f"https://{domain}/sheets/{token}"
    return json.dumps({
        "sheet_url": sheet_url,
        "token": token,
        "row_count": row_count - 1,
        "group_count": len(groups),
    }, ensure_ascii=False)
