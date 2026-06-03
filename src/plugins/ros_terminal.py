"""ROS terminal plugin: owns the Kian-side planner node and executor thread."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Optional

from src.plugins.base import Plugin
from src.ros.goal_selection_store import get_goal_selection_store
from src.ros.kian_global_planner import KianGlobalPlanner
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class RosTerminalPlugin(Plugin):
    name = "ros_terminal"
    priority = 15

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.store = get_goal_selection_store()
        self.planner: Optional[KianGlobalPlanner] = None
        self._executor = None
        self._node = None
        self._thread: Optional[threading.Thread] = None

    async def setup(self, app: Any) -> None:
        self.app = app
        config = app.config.get_config("GLOBAL_PLANNER", {}) or {}
        if not bool(config.get("enabled", True)):
            logger.info("RosTerminalPlugin: disabled by config")
            return
        root = Path(__file__).resolve().parents[2]
        try:
            self.planner = KianGlobalPlanner(config, root)
        except Exception as exc:
            logger.error("RosTerminalPlugin: map initialization failed: %s", exc, exc_info=True)

    async def start(self) -> None:
        if self.planner is None:
            return
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
            if not rclpy.ok():
                rclpy.init(args=None)
            self._node = Node("kian_ros_terminal")
            self.planner.attach_ros(self._node)
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)
            self._thread = threading.Thread(target=self._executor.spin, name="kian_ros_terminal_executor", daemon=True)
            self._thread.start()
            self._started = True
        except Exception as exc:
            logger.error("RosTerminalPlugin: ROS startup failed: %s", exc, exc_info=True)

    async def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        self._started = False

    async def dispatch_selected_goal(self, goal_type: int) -> str:
        selected = self.store.latest()
        if selected is None:
            raise RuntimeError("尚未在 Web 地图框选目标")
        if self.planner is None:
            raise RuntimeError("全局规划器未初始化")
        return json.dumps(self.planner.dispatch_selected(selected, goal_type), ensure_ascii=False)

    async def planner_status(self) -> str:
        status = self.planner.status() if self.planner is not None else {"enabled": False, "available": False}
        status["selected_goal"] = self.store.latest()
        return json.dumps(status, ensure_ascii=False)


_plugin: Optional[RosTerminalPlugin] = None


def get_ros_terminal_plugin() -> RosTerminalPlugin:
    global _plugin
    if _plugin is None:
        _plugin = RosTerminalPlugin()
    return _plugin
