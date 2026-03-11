"""Agent 创建与配置 — LangChain Agent 核心模块。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Generator

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from .config import get_config
from .prompts import get_system_prompt
from .tools import get_all_tools


# ---------------------------------------------------------------------------
# Tool call logging callback
# ---------------------------------------------------------------------------

class ToolCallLogger(BaseCallbackHandler):
    """打印每个工具调用的开始时间、参数、结果和耗时。"""

    def __init__(self) -> None:
        self._start_times: dict[str, float] = {}

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        tool_name = serialized.get("name", "unknown")
        now = time.time()
        rid = str(run_id)
        self._start_times[rid] = now
        ts = datetime.fromtimestamp(now).strftime("%H:%M:%S")

        # Truncate long input for readability
        preview = input_str[:200] + "..." if len(input_str) > 200 else input_str
        print(f"\n{'='*60}")
        print(f"🔧 [{ts}] 工具调用开始: {tool_name}")
        print(f"   参数: {preview}")
        print(f"{'='*60}", flush=True)

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        now = time.time()
        rid = str(run_id)
        start = self._start_times.pop(rid, now)
        elapsed = now - start
        ts = datetime.fromtimestamp(now).strftime("%H:%M:%S")

        # Truncate long output for readability
        output_str = str(output)
        preview = output_str[:300] + "..." if len(output_str) > 300 else output_str
        print(f"\n{'='*60}")
        print(f"✅ [{ts}] 工具调用结束 (耗时 {elapsed:.1f}s)")
        print(f"   结果: {preview}")
        print(f"{'='*60}", flush=True)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        now = time.time()
        rid = str(run_id)
        start = self._start_times.pop(rid, now)
        elapsed = now - start
        ts = datetime.fromtimestamp(now).strftime("%H:%M:%S")
        print(f"\n{'='*60}")
        print(f"❌ [{ts}] 工具调用失败 (耗时 {elapsed:.1f}s)")
        print(f"   错误: {error}")
        print(f"{'='*60}", flush=True)


def create_feishu_agent(thread_id: str | None = None):
    """Create and return a configured Feishu bot agent.

    Args:
        thread_id: Optional thread ID for conversation persistence.

    Returns:
        A LangChain agent instance.
    """
    config = get_config()

    kwargs = {
        "model": config["OPENAI_MODEL"],
        "temperature": 0.3,
    }
    if config["OPENAI_API_KEY"]:
        kwargs["api_key"] = config["OPENAI_API_KEY"]
    if config["OPENAI_BASE_URL"]:
        base_url = config["OPENAI_BASE_URL"]
        if base_url.endswith("/chat/completions"):
            base_url = base_url.replace("/chat/completions", "")
        kwargs["base_url"] = base_url

    model = ChatOpenAI(**kwargs)

    tools = get_all_tools()
    system_prompt = get_system_prompt()

    checkpointer = InMemorySaver()
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )

    return agent


def run_agent(
    agent,
    user_message: str,
    thread_id: str = "default",
    metadata: dict | None = None,
) -> str:
    """Run the agent with a user message and return the response.

    Args:
        agent: The agent instance from create_feishu_agent().
        user_message: The user's input message.
        thread_id: Thread ID for conversation context.
        metadata: Optional metadata dict for LangSmith tracing.

    Returns:
        The agent's response text.
    """
    run_config: dict = {"configurable": {"thread_id": thread_id}}
    run_config["run_name"] = "飞书Bot Agent对话"
    run_config["callbacks"] = [ToolCallLogger()]
    if metadata:
        run_config["metadata"] = metadata

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config=run_config,
        )
    except Exception as e:
        print(f"[agent] invoke error: {type(e).__name__}: {e}")
        raise

    messages = result.get("messages", [])

    # Debug: print message types for diagnosis
    msg_summary = []
    for msg in messages:
        mtype = getattr(msg, "type", getattr(msg, "role", "?"))
        has_content = bool(getattr(msg, "content", None))
        has_tool_calls = bool(getattr(msg, "tool_calls", None))
        msg_summary.append(f"{mtype}(content={has_content}, tool_calls={has_tool_calls})")
    print(f"[agent] messages ({len(messages)}): {msg_summary}")

    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            return msg.content
        if hasattr(msg, "role") and msg.role == "assistant" and msg.content:
            return msg.content

    return "Agent 未返回有效响应。"


def run_agent_stream(
    agent,
    user_message: str,
    thread_id: str = "default",
    metadata: dict | None = None,
) -> Generator[str, None, None]:
    """Run the agent with streaming output.

    Args:
        agent: The agent instance from create_feishu_agent().
        user_message: The user's input message.
        thread_id: Thread ID for conversation context.
        metadata: Optional metadata dict for LangSmith tracing.

    Yields:
        Chunks of the agent's response text.
    """
    run_config: dict = {"configurable": {"thread_id": thread_id}}
    run_config["run_name"] = "飞书Bot Agent对话"
    run_config["callbacks"] = [ToolCallLogger()]
    if metadata:
        run_config["metadata"] = metadata

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": user_message}]},
        config=run_config,
        stream_mode="messages",
    ):
        if isinstance(chunk, tuple):
            msg, _meta = chunk
            if hasattr(msg, "content") and msg.content:
                if hasattr(msg, "type") and msg.type == "ai":
                    yield msg.content
        elif hasattr(chunk, "content") and chunk.content:
            yield chunk.content


# === LangGraph Studio 入口 ===
# 模块级 compiled graph，供 langgraph dev / Studio 使用
# Agent Server 自动管理 checkpointing，无需 InMemorySaver
_config = get_config()
_kwargs = {"model": _config["OPENAI_MODEL"], "temperature": 0.3}
if _config["OPENAI_API_KEY"]:
    _kwargs["api_key"] = _config["OPENAI_API_KEY"]
if _config["OPENAI_BASE_URL"]:
    _base_url = _config["OPENAI_BASE_URL"]
    if _base_url.endswith("/chat/completions"):
        _base_url = _base_url.replace("/chat/completions", "")
    _kwargs["base_url"] = _base_url

agent = create_agent(
    model=ChatOpenAI(**_kwargs),
    tools=get_all_tools(),
    system_prompt=get_system_prompt(),
)
