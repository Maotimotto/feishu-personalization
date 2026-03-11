"""Agent 系统提示词管理。"""

from __future__ import annotations

import os

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def get_system_prompt() -> str:
    """Load and return the agent system prompt."""
    path = os.path.join(_PROMPTS_DIR, "system_prompt.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return _DEFAULT_SYSTEM_PROMPT


_DEFAULT_SYSTEM_PROMPT = """你是一个专业的飞书群聊消息分析 Agent。

你可以帮助用户完成以下任务：
1. 从飞书群聊中获取消息历史
2. 从消息中提取 URL 链接
3. 获取链接的标题和内容
4. 使用 LLM 对标题进行热词分类
5. 创建飞书文档和表格
6. 发送飞书消息
7. 导出数据到 JSON/Excel 文件

所有输出使用中文。"""
