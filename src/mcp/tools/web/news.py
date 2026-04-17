"""
新闻查询工具 —— 抓取主流新闻 RSS，无需 API Key。
支持多个分类：综合、科技、财经、娱乐、体育、国际。
"""
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# RSS 源配置（免费可访问）
RSS_SOURCES = {
    "综合": [
        ("新浪综合",   "https://rss.sina.com.cn/news/china/focus15.xml"),
        ("网易头条",   "http://news.163.com/special/00011K6L/rss_newstop.xml"),
    ],
    "科技": [
        ("36氪",       "https://36kr.com/feed"),
        ("新浪科技",   "https://rss.sina.com.cn/tech/rollnews/rss.xml"),
    ],
    "财经": [
        ("新浪财经",   "https://rss.sina.com.cn/finance/most_view/rss.xml"),
    ],
    "娱乐": [
        ("新浪娱乐",   "https://rss.sina.com.cn/ent/rollnews/rss.xml"),
    ],
    "体育": [
        ("新浪体育",   "https://rss.sina.com.cn/sports/rollnews/rss.xml"),
    ],
    "国际": [
        ("新浪国际",   "https://rss.sina.com.cn/news/world/focus15.xml"),
    ],
}

# 分类别名映射
_CATEGORY_ALIAS = {
    "科技": "科技", "技术": "科技", "it": "科技", "互联网": "科技",
    "财经": "财经", "金融": "财经", "股票": "财经", "经济": "财经",
    "娱乐": "娱乐", "明星": "娱乐", "影视": "娱乐",
    "体育": "体育", "足球": "体育", "篮球": "体育",
    "国际": "国际", "世界": "国际", "外国": "国际",
    "综合": "综合", "头条": "综合", "热点": "综合", "今日": "综合",
}


def _fetch_rss(url: str, max_items: int = 8) -> list[dict]:
    """抓取 RSS，返回 [{title, link, pubDate}] 列表。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        raw = resp.read()
    # 尝试解码
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    root = ET.fromstring(text)
    items = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # 标准 RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link")  or "").strip()
        date  = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "link": link, "date": date})
        if len(items) >= max_items:
            break

    # Atom feed 兜底
    if not items:
        for entry in root.findall(".//atom:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            title = (title_el.text if title_el is not None else "").strip()
            if title:
                items.append({"title": title, "link": "", "date": ""})
            if len(items) >= max_items:
                break

    return items


async def query_news(args: dict) -> str:
    """查询最新新闻，支持分类筛选。"""
    category_raw = (args.get("category") or "综合").strip().lower()
    count = min(int(args.get("count") or 5), 8)

    # 归一化分类
    category = _CATEGORY_ALIAS.get(category_raw, "综合")
    sources = RSS_SOURCES.get(category, RSS_SOURCES["综合"])

    logger.info(f"[新闻] 查询分类：{category}，条数：{count}")

    all_items = []
    for source_name, url in sources:
        try:
            items = _fetch_rss(url, max_items=count)
            all_items.extend(items)
            if len(all_items) >= count:
                break
        except Exception as e:
            logger.warning(f"[新闻] {source_name} 抓取失败：{e}")

    if not all_items:
        return f"暂时抓不到{category}新闻（网络或 RSS 源问题），稍后再试。"

    # 只返回标题列表，让 LLM 口语化总结，不要原样输出
    titles = [item["title"] for item in all_items[:count]]
    return f"{category}新闻标题（{len(titles)}条）：\n" + "\n".join(f"- {t}" for t in titles)
