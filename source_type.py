"""Determine source type from URL domain — pure rule-based, no LLM."""

from __future__ import annotations

from urllib.parse import urlparse

# Domain suffix → display name mapping.
# Checked from most-specific to least-specific.
_DOMAIN_MAP: list[tuple[str, str]] = [
    # --- Social / short-video platforms ---
    ("mp.weixin.qq.com", "微信公众号"),
    ("weixin.qq.com", "微信"),
    ("v.douyin.com", "抖音"),
    ("www.douyin.com", "抖音"),
    ("www.iesdouyin.com", "抖音"),
    ("douyin.com", "抖音"),
    ("weibo.com", "微博"),
    ("m.weibo.cn", "微博"),
    ("weibo.cn", "微博"),
    ("bilibili.com", "B站"),
    ("b23.tv", "B站"),
    ("zhihu.com", "知乎"),
    ("xiaohongshu.com", "小红书"),
    ("xhslink.com", "小红书"),
    ("toutiao.com", "今日头条"),
    ("kuaishou.com", "快手"),
    ("channels.weixin.qq.com", "视频号"),

    # --- Finance / business media (20+) ---
    ("finance.sina.com.cn", "新浪财经"),
    ("cj.sina.com.cn", "新浪财经"),
    ("finance.qq.com", "腾讯财经"),
    ("money.163.com", "网易财经"),
    ("finance.163.com", "网易财经"),
    ("finance.ifeng.com", "凤凰财经"),
    ("wallstreetcn.com", "华尔街见闻"),
    ("cls.cn", "财联社"),
    ("yuncaijing.com", "云财经"),
    ("eastmoney.com", "东方财富"),
    ("10jqka.com.cn", "同花顺"),
    ("hexun.com", "和讯网"),
    ("stcn.com", "证券时报"),
    ("cnstock.com", "中国证券网"),
    ("cs.com.cn", "中证网"),
    ("ssajax.cn", "上交所"),
    ("szse.cn", "深交所"),
    ("sse.com.cn", "上交所"),
    ("caixin.com", "财新网"),
    ("yicai.com", "第一财经"),
    ("21jingji.com", "21世纪经济"),
    ("jrj.com.cn", "金融界"),
    ("cbndata.com", "CBNData"),
    ("36kr.com", "36氪"),
    ("huxiu.com", "虎嗅"),
    ("gelonghui.com", "格隆汇"),
    ("xuangubao.cn", "选股宝"),
    ("xueqiu.com", "雪球"),
    ("futunn.com", "富途牛牛"),
    ("laohu8.com", "老虎证券"),
    ("jin10.com", "金十数据"),
    ("fx168.com", "FX168财经"),
    ("fxstreet.com", "FXStreet"),

    # --- Tech / news ---
    ("thepaper.cn", "澎湃新闻"),
    ("163.com", "网易"),
    ("sina.com.cn", "新浪"),
    ("sohu.com", "搜狐"),
    ("qq.com", "腾讯"),
    ("ifeng.com", "凤凰网"),
    ("people.com.cn", "人民网"),
    ("xinhuanet.com", "新华网"),
    ("chinanews.com", "中新网"),
    ("cctv.com", "央视网"),

    # --- International finance ---
    ("reuters.com", "路透社"),
    ("bloomberg.com", "彭博社"),
    ("ft.com", "金融时报"),
    ("wsj.com", "华尔街日报"),
    ("cnbc.com", "CNBC"),
    ("marketwatch.com", "MarketWatch"),
    ("investing.com", "英为财情"),
    ("seekingalpha.com", "SeekingAlpha"),
    ("yahoo.com/finance", "雅虎财经"),
]

# Video domains — URLs matching these are considered video links
_VIDEO_DOMAINS: set[str] = {
    "v.douyin.com", "www.douyin.com", "www.iesdouyin.com", "douyin.com",
    "kuaishou.com",
    "bilibili.com", "b23.tv",
    "channels.weixin.qq.com",
}


def get_source_type(url: str) -> str:
    """Return a human-readable source type for the given URL."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return "其他"

    host = host.lower().removeprefix("www.")

    for domain, label in _DOMAIN_MAP:
        # Strip www. from the mapping domain too for comparison
        d = domain.lower().removeprefix("www.")
        if host == d or host.endswith("." + d):
            return label

    # Fallback: extract primary domain
    parts = host.rsplit(".", 2)
    if len(parts) >= 2:
        return parts[-2] + "." + parts[-1]
    return host or "其他"


def is_video_url(url: str) -> bool:
    """Check if a URL points to a video platform."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False

    host = host.lower()
    for vd in _VIDEO_DOMAINS:
        if host == vd or host.endswith("." + vd):
            return True
    return False
