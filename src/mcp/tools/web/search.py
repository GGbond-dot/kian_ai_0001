"""
网络搜索工具 —— 双路策略：
  1. 维基百科中文 API（知识性问题）
  2. Bing 搜索结果抓取（实时/热点信息）
无需 API Key，在中国大陆可用。
"""
import json
import re
import ssl
import urllib.parse
import urllib.request
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_get(url: str, timeout: int = 8, headers: dict | None = None) -> bytes:
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return r.read()


def _wikipedia_search(query: str) -> str | None:
    """调用维基百科中文 API 返回摘要段落。"""
    search_url = (
        "https://zh.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1,
            "utf8": 1,
        })
    )
    raw = _http_get(search_url, timeout=8)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    results = data.get("query", {}).get("search", [])
    if not results:
        return None

    title = results[0]["title"]
    snippet_html = results[0].get("snippet", "")
    # 去掉 HTML 标签
    snippet = re.sub(r"<[^>]+>", "", snippet_html).strip()

    # 再取正文摘要
    extract_url = (
        "https://zh.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "titles": title,
            "format": "json",
            "utf8": 1,
            "exsentences": 4,
        })
    )
    raw2 = _http_get(extract_url, timeout=8)
    data2 = json.loads(raw2.decode("utf-8", errors="replace"))
    pages = data2.get("query", {}).get("pages", {})
    extract = ""
    for page in pages.values():
        extract = (page.get("extract") or "").strip()
        break

    if extract and len(extract) > 20:
        return f"【{title}】\n{extract[:600]}"
    elif snippet:
        return f"【{title}】\n{snippet[:300]}"
    return None


def _bing_search(query: str) -> str | None:
    """Bing 搜索抓取前几条结果的标题和摘要。"""
    url = (
        "https://cn.bing.com/search?"
        + urllib.parse.urlencode({"q": query, "setlang": "zh-CN", "mkt": "zh-CN"})
    )
    raw = _http_get(url, timeout=10)
    html = raw.decode("utf-8", errors="replace")

    # 提取 <h2><a ...>标题</a></h2> 下面紧跟的摘要
    # Bing 结果里标题在 <h2> 内，摘要在 class="b_caption" 或 <p> 里
    # 用正则提取 <li class="b_algo"> 块内的标题 + 摘要
    snippets = []
    blocks = re.findall(
        r'<h2><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>.*?'
        r'<div class="b_caption">.*?<p[^>]*>(.*?)</p>',
        html,
        re.DOTALL,
    )
    for _, title_html, snippet_html in blocks[:5]:
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet_html).strip()
        if title:
            snippets.append(f"• {title}：{snippet[:120]}" if snippet else f"• {title}")

    if snippets:
        return "搜索结果：\n" + "\n".join(snippets)

    # 备用：直接抓所有 <p> 文本
    paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
    texts = [re.sub(r"<[^>]+>", "", p).strip() for p in paras]
    texts = [t for t in texts if len(t) > 30][:5]
    if texts:
        return "搜索结果：\n" + "\n".join(f"· {t[:150]}" for t in texts)

    return None


async def web_search(args: dict) -> str:
    """
    联网搜索工具。遇到不知道的知识、实时信息、热点事件时调用。
    返回搜索摘要供 AI 参考后用自己的话回答用户。
    """
    query = (args.get("query") or "").strip()
    if not query:
        return "请提供搜索关键词。"

    logger.info(f"[搜索] 查询：{query}")

    errors = []

    # 策略 1：维基百科（适合知识性问题）
    try:
        result = _wikipedia_search(query)
        if result:
            logger.info(f"[搜索] 维基百科命中")
            return result
    except Exception as e:
        errors.append(f"Wiki: {e}")
        logger.warning(f"[搜索] 维基百科失败：{e}")

    # 策略 2：Bing 搜索
    try:
        result = _bing_search(query)
        if result:
            logger.info(f"[搜索] Bing 命中")
            return result
    except Exception as e:
        errors.append(f"Bing: {e}")
        logger.warning(f"[搜索] Bing 失败：{e}")

    return f"搜索失败，网络连接问题：{'; '.join(errors)}"

