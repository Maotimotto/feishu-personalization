"""Tool: 导出数据到 JSON 和 Excel 文件。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import openpyxl
from langchain_core.tools import tool

_EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"


@tool
def export_data(data_json: str, chat_id: str = "", export_format: str = "both") -> str:
    """将消息和链接数据导出到本地文件（JSON 和/或 Excel）。

    ## 何时使用
    - 用户要求本地备份数据时
    - 需要导出 Excel 文件供后续使用时
    - 这是可选步骤，通常在创建飞书表格之后作为补充

    ## 何时不用
    - 用户只需要飞书在线文档/表格时 → 使用 create_feishu_doc 或 create_feishu_spreadsheet
    - 没有可导出的数据时

    ## 输入要求
    - JSON 导出：可接受任意 JSON 数据
    - Excel 导出：需要包含 groups、titles、results 字段（与 create_feishu_spreadsheet 相同的合并数据）

    ## 注意
    - 文件保存在项目根目录的 exports/ 文件夹下
    - 文件名格式为 {日期}_{chat_id}.json / .xlsx

    Args:
        data_json: 要导出的 JSON 数据。Excel 导出需包含 groups、titles、results 字段。
        chat_id: 聊天 ID，用于文件命名。
        export_format: 导出格式 — "json"、"excel" 或 "both"。默认 "both"。

    Returns:
        JSON 字符串，包含导出文件路径列表。
    """
    data = json.loads(data_json)
    today = datetime.now().strftime("%Y-%m-%d")
    _EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    result = {"files": []}

    # JSON export
    if export_format in ("json", "both"):
        export_data_obj = {
            "chat_id": chat_id,
            "date": today,
            "data": data,
        }
        json_path = _EXPORTS_DIR / f"{today}_{chat_id}.json"
        json_path.write_text(
            json.dumps(export_data_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["files"].append({"format": "json", "path": str(json_path)})

    # Excel export
    if export_format in ("excel", "both"):
        groups = data.get("groups", {})
        titles = data.get("titles", [])
        results = data.get("results", [])
        search_results_map = data.get("search_results_map", {})
        analysis_map = data.get("analysis_map", {})

        if groups and titles:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = datetime.now().strftime("%Y%m%d")

            # Header
            ws.append(["热词", "选题", "来源链接", "来源类型", "文案", "事件分析"])

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
                    ws.append([
                        keyword if first else "",
                        display_title,
                        url,
                        source_type,
                        content,
                        analysis_text if first else "",
                    ])
                    first = False

            xlsx_path = _EXPORTS_DIR / f"{today}_{chat_id}.xlsx"
            wb.save(xlsx_path)
            result["files"].append({"format": "excel", "path": str(xlsx_path)})

    return json.dumps(result, ensure_ascii=False)
