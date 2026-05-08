"""
日程管理器 负责日程数据的存储、查询、更新等核心功能.
"""

import os
from typing import List

from src.utils.logging_config import get_logger

from .database import get_calendar_database
from .models import CalendarEvent

logger = get_logger(__name__)


class CalendarManager:
    """
    日程管理器.
    """

    def __init__(self):
        self.db = get_calendar_database()
        # 尝试从旧的JSON文件迁移数据
        self._migrate_from_json_if_exists()

    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        """
        初始化并注册所有日程管理工具.
        """
        from .tools import (
            create_event,
            delete_event,
            get_upcoming_events,
            update_event,
        )

        # 创建日程
        create_event_props = PropertyList(
            [
                Property("title", PropertyType.STRING),
                Property("start_time", PropertyType.STRING),
                Property("end_time", PropertyType.STRING, default_value=""),
                Property("description", PropertyType.STRING, default_value=""),
                Property("category", PropertyType.STRING, default_value="默认"),
                Property("reminder_minutes", PropertyType.INTEGER, default_value=15),
            ]
        )
        add_tool(
            (
                "self.calendar.create_event",
                "创建日程。start_time 用 ISO 格式 '2024-01-01T10:00:00'。"
                "category 可选 默认/工作/个人/会议/提醒。end_time 不传时自动计算。",
                create_event_props,
                create_event,
            )
        )

        # 查询即将到来的日程
        upcoming_events_props = PropertyList(
            [Property("hours", PropertyType.INTEGER, default_value=24)]
        )
        add_tool(
            (
                "self.calendar.get_upcoming_events",
                "查询未来 N 小时内的日程（默认 24h）。用户问「接下来有什么/最近有什么会」时调用。",
                upcoming_events_props,
                get_upcoming_events,
            )
        )

        # 更新日程
        update_event_props = PropertyList(
            [
                Property("event_id", PropertyType.STRING),
                Property("title", PropertyType.STRING, default_value=""),
                Property("start_time", PropertyType.STRING, default_value=""),
                Property("end_time", PropertyType.STRING, default_value=""),
                Property("description", PropertyType.STRING, default_value=""),
                Property("category", PropertyType.STRING, default_value=""),
                Property("reminder_minutes", PropertyType.INTEGER, default_value=15),
            ]
        )
        add_tool(
            (
                "self.calendar.update_event",
                "修改日程。仅填要改的字段，未填字段保持不变。需要先有 event_id。",
                update_event_props,
                update_event,
            )
        )

        # 删除日程
        delete_event_props = PropertyList([Property("event_id", PropertyType.STRING)])
        add_tool(
            (
                "self.calendar.delete_event",
                "按 event_id 删除一条日程。",
                delete_event_props,
                delete_event,
            )
        )

    def _migrate_from_json_if_exists(self):
        """
        从旧的JSON文件迁移数据（如果存在）
        """
        # 检查项目根目录中的旧JSON文件
        from src.utils.resource_finder import get_project_root, get_user_cache_dir

        try:
            project_root = get_project_root()
            json_file = project_root / "cache" / "calendar_data.json"
        except Exception:
            # 如果无法获取项目根目录，检查用户缓存目录
            user_cache_dir = get_user_cache_dir(create=False)
            json_file = user_cache_dir / "calendar_data.json"

        if os.path.exists(json_file):
            logger.info("发现旧的JSON数据文件，开始迁移到SQLite...")
            if self.db.migrate_from_json(json_file):
                # 迁移成功后备份原文件
                backup_file = f"{json_file}.backup"
                os.rename(json_file, backup_file)
                logger.info(f"数据迁移完成，原文件已备份为: {backup_file}")
            else:
                logger.warning("数据迁移失败，保留原JSON文件")

    def add_event(self, event: CalendarEvent) -> bool:
        """
        添加事件.
        """
        return self.db.add_event(event.to_dict())

    def get_events(
        self, start_date: str = None, end_date: str = None, category: str = None
    ) -> List[CalendarEvent]:
        """
        获取事件列表.
        """
        try:
            events_data = self.db.get_events(start_date, end_date, category)
            return [CalendarEvent.from_dict(event_data) for event_data in events_data]
        except Exception as e:
            logger.error(f"获取日程失败: {e}")
            return []

    def update_event(self, event_id: str, **kwargs) -> bool:
        """
        更新事件.
        """
        return self.db.update_event(event_id, **kwargs)

    def delete_event(self, event_id: str) -> bool:
        """
        删除事件.
        """
        return self.db.delete_event(event_id)

    def delete_events_batch(
        self,
        start_date: str = None,
        end_date: str = None,
        category: str = None,
        delete_all: bool = False,
    ):
        """
        批量删除事件.
        """
        return self.db.delete_events_batch(start_date, end_date, category, delete_all)

    def get_categories(self) -> List[str]:
        """
        获取所有分类.
        """
        return self.db.get_categories()


# 全局管理器实例
_calendar_manager = None


def get_calendar_manager() -> CalendarManager:
    """
    获取日程管理器单例.
    """
    global _calendar_manager
    if _calendar_manager is None:
        _calendar_manager = CalendarManager()
    return _calendar_manager
