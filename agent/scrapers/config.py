"""Configuration constants for the precious metals news aggregator."""

import os

# Project root: the parent of the scrapers/ directory
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_COMMON_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# --- CLS (财联社) 头条 ---
CLS_API_URL = "https://www.cls.cn/v3/depth/home/assembled/1000"
CLS_API_PARAMS = {
    "app": "CailianpressWeb",
    "os": "web",
    "sv": "8.4.6",
    "sign": "9f8797a1f4de66c2370f7a03990d2737",
}
CLS_HEADERS = {
    "Referer": "https://www.cls.cn/depth?id=1000",
    "User-Agent": _COMMON_UA,
}

# --- Jin10 (金十) ---
JIN10_URL = "https://xnews.jin10.com/"
JIN10_HEADERS = {
    "User-Agent": _COMMON_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# --- Futu (富途) ---
FUTU_URL = "https://news.futunn.com/main?lang=zh-cn"

# --- Eastmoney 资讯精华 ---
EASTMONEY_NEWS_API_URL = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
EASTMONEY_NEWS_PARAMS = {
    "client": "web",
    "biz": "web_news_col",
    "column": "345",
    "order": "1",
    "needInteractData": "0",
    "page_index": "1",
    "page_size": "30",
    "req_trace": "cywjh",
    "callback": "cb",
}
EASTMONEY_NEWS_HEADERS = {
    "Referer": "https://finance.eastmoney.com/a/cywjh.html",
    "User-Agent": _COMMON_UA,
}

# --- Precious metals keywords ---
PRECIOUS_METALS_KEYWORDS = [
    # Chinese
    "黄金", "白银", "贵金属", "金价", "银价", "金矿", "银矿",
    "铂金", "钯金", "伦敦金", "伦敦银", "纸黄金", "实物黄金",
    "黄金期货", "白银期货", "黄金ETF", "白银ETF", "金条", "金币",
    "央行购金", "黄金储备", "避险",
    # English / Ticker symbols
    "COMEX", "XAUUSD", "XAGUSD", "GLD", "SLV",
    "gold", "silver", "precious metal",
]

# --- Output ---
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "data", "新闻文件")
