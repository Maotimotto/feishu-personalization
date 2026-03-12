"""Agent 配置管理 — API Keys、模型选择等。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from project root
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_BASE_DIR, ".env")
load_dotenv(_ENV_PATH)


def get_config() -> dict[str, str]:
    """Get configuration from environment variables."""
    return {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY", ""),
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL", ""),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL", "gpt-4o"),
        "LLM_TEMPERATURE": float(os.getenv("LLM_TEMPERATURE", "1")),
        "LLM_MAX_TOKENS": int(os.getenv("LLM_MAX_TOKENS", "16384")),
        "LLM_TIMEOUT": int(os.getenv("LLM_TIMEOUT", "180")),
        "LLM_MAX_RETRIES": int(os.getenv("LLM_MAX_RETRIES", "3")),
        "FEISHU_APP_ID": os.getenv("FEISHU_APP_ID", ""),
        "FEISHU_APP_SECRET": os.getenv("FEISHU_APP_SECRET", ""),
        "FEISHU_DOMAIN": os.getenv("FEISHU_DOMAIN", "feishu.cn"),
        "CHAT_IDS": os.getenv("CHAT_IDS", ""),
        "TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", ""),
        "EXA_API_KEY": os.getenv("EXA_API_KEY", ""),
        "SCHEDULE_TIME": os.getenv("SCHEDULE_TIME", "08:00"),
    }


def get_llm():
    """Get the configured LLM instance."""
    from langchain_openai import ChatOpenAI

    config = get_config()

    kwargs = {
        "model": config["OPENAI_MODEL"],
        "temperature": config["LLM_TEMPERATURE"],
        "max_tokens": config["LLM_MAX_TOKENS"],
        "timeout": config["LLM_TIMEOUT"],
        "max_retries": config["LLM_MAX_RETRIES"],
    }

    if config["OPENAI_API_KEY"]:
        kwargs["api_key"] = config["OPENAI_API_KEY"]
    if config["OPENAI_BASE_URL"]:
        base_url = config["OPENAI_BASE_URL"]
        if base_url.endswith("/chat/completions"):
            base_url = base_url.replace("/chat/completions", "")
        kwargs["base_url"] = base_url

    return ChatOpenAI(**kwargs)
