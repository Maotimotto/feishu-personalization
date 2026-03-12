from .cls import CLSScraper
from .cls_morning import CLSMorningScraper
from .jin10 import Jin10Scraper
from .futu import FutuScraper
from .eastmoney_news import EastmoneyNewsScraper
from .jin10_breakfast import get_today_breakfast, fetch_breakfast_content
from .base import Article, BaseScraper
from .filters import tag_precious_metals
from .formatter import generate_report

__all__ = [
    "CLSScraper",
    "CLSMorningScraper",
    "Jin10Scraper",
    "FutuScraper",
    "EastmoneyNewsScraper",
    "Article",
    "BaseScraper",
    "tag_precious_metals",
    "generate_report",
    "get_today_breakfast",
    "fetch_breakfast_content",
]
