from .weather import query_weather
from .news import query_news
from .search import web_search


class WebToolsManager:
    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        add_tool((
            "web.get_weather",
            (
                "查询实时天气和未来三天预报。当用户问天气、气温、下雨、穿衣等问题时调用。"
                "city: 城市名（中文或英文均可，如'北京'、'上海'、'London'）。"
                "不知道城市时可问用户，或根据对话上下文推断。"
            ),
            PropertyList([
                Property("city", PropertyType.STRING),
            ]),
            query_weather,
        ))

        add_tool((
            "web.get_news",
            (
                "获取最新新闻资讯。当用户问新闻、热点、今天发生什么、头条等问题时调用。"
                "category: 新闻分类，可选值：综合、科技、财经、娱乐、体育、国际（默认综合）。"
                "count: 返回条数（默认 5）。"
                "工具返回新闻标题列表，你应该用自然中文直接楔要 2~3 条最展示的新闻内容说给用户听，"
                "不要按号列表全部展示。"
            ),
            PropertyList([
                Property("category", PropertyType.STRING, default_value="综合"),
                Property("count",    PropertyType.INTEGER, default_value=5),
            ]),
            query_news,
        ))

        add_tool((
            "web.search",
            (
                "联网搜索工具。当遇到不确定的知识、实时信息、热点事件、具体人/物/地等问题时调用。"
                "你应该用自然语言回答，不要把搜索结果原文输出。"
                "query: 搜索关键词，尽量精简。"
            ),
            PropertyList([
                Property("query", PropertyType.STRING),
            ]),
            web_search,
        ))


_manager = None


def get_web_manager():
    global _manager
    if _manager is None:
        _manager = WebToolsManager()
    return _manager
