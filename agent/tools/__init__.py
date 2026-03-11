from .fetch_messages import fetch_chat_messages
from .extract_urls import extract_urls
from .fetch_url_content import fetch_url_content
from .classify_titles import classify_titles
from .create_feishu_doc import create_feishu_doc
from .create_spreadsheet import create_feishu_spreadsheet
from .send_message import send_feishu_message
from .export_data import export_data
from .web_search import search_web
from .refine_keywords import refine_keywords


def get_all_tools() -> list:
    """Return all available agent tools."""
    return [
        fetch_chat_messages,
        extract_urls,
        fetch_url_content,
        classify_titles,
        refine_keywords,
        create_feishu_doc,
        create_feishu_spreadsheet,
        send_feishu_message,
        export_data,
        search_web,
    ]


__all__ = [
    "get_all_tools",
    "fetch_chat_messages",
    "extract_urls",
    "fetch_url_content",
    "classify_titles",
    "refine_keywords",
    "create_feishu_doc",
    "create_feishu_spreadsheet",
    "send_feishu_message",
    "export_data",
    "search_web",
]
