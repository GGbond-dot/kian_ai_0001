"""ROS terminal plugin: owns the Kian-side planner nodes and executor thread.

多机改造:为每架启用的无人机创建一个 KianGlobalPlanner,共用一个 ROS node + executor +
spin 线程(避免线程膨胀)。无 DRONES 配置时回退单机,行为与改造前一致。
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Optional

from src.plugins.base import Plugin
from src.ros.drone_config import DroneConfig, load_drone_configs
from src.ros.goal_selection_store import get_goal_selection_store
from src.ros.kian_global_planner import KianGlobalPlanner
from src.ros.multi_drone_coordinator import MultiDroneCoordinator
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class RosTerminalPlugin(Plugin):
    name = "ros_terminal"
    priority = 15

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.store = get_goal_selection_store()
        self.planners: dict[str, KianGlobalPlanner] = {}
        self._drone_configs: list[DroneConfig] = []
        self._default_key: str = ""
        self.coordinator: Optional[MultiDroneCoordinator] = None
        self._executor = None
        self._node = None
        self._thread: Optional[threading.Thread] = None
        self._landing_monitor_task: Optional[asyncio.Task] = None

    # 兼容旧调用方(ui.py / vision_plugin):返回默认机 planner
    @property
    def planner(self) -> Optional[KianGlobalPlanner]:
        return self.planners.get(self._default_key) or next(iter(self.planners.values()), None)

    def _resolve_key(self, drone_key: Optional[str]) -> str:
        """把传入 drone_key 归一化为一个已启用的机号;None / 未知 → 默认机。"""
        if drone_key and drone_key in self.planners:
            return drone_key
        return self._default_key

    def planner_for(self, drone_key: Optional[str] = None) -> Optional[KianGlobalPlanner]:
        """按 drone_key 取 planner;None / 未知 → 默认机 planner。"""
        return self.planners.get(self._resolve_key(drone_key))

    def set_path_callback(self, callback) -> None:
        """给每架 planner 设 path 回调,绑定各自 drone_key(回调签名 (points, z, drone_key))。"""
        for key, planner in self.planners.items():
            planner.set_path_callback(lambda pts, z, k=key: callback(pts, z, k))

    async def setup(self, app: Any) -> None:
        self.app = app
        configs = [c for c in load_drone_configs(app.config) if c.enabled]
        if not configs:
            logger.info("RosTerminalPlugin: no enabled drones, disabled")
            return
        root = Path(__file__).resolve().parents[2]
        # 默认机:MULTI_DRONE.default_drone_key 命中则用之,否则第一架
        default_key = str(app.config.get_config("MULTI_DRONE.default_drone_key", "") or "")
        self._drone_configs = configs
        self._default_key = default_key if any(c.key == default_key for c in configs) else configs[0].key
        for cfg in configs:
            try:
                self.planners[cfg.key] = KianGlobalPlanner(cfg.planner_config(), root)
                logger.info("RosTerminalPlugin: planner ready key=%s label=%s", cfg.key, cfg.label)
            except Exception as exc:
                logger.error("RosTerminalPlugin: planner init failed key=%s: %s", cfg.key, exc, exc_info=True)
        self.coordinator = MultiDroneCoordinator(self)

    async def start(self) -> None:
        if not self.planners:
            return
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
            if not rclpy.ok():
                rclpy.init(args=None)
            self._node = Node("kian_ros_terminal")
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)
            for key, planner in self.planners.items():
                planner.attach_ros(self._node)
                planner.set_completion_callback(
                    lambda result, k=key: self._on_mission_complete(k, result)
                )
            self._thread = threading.Thread(
                target=self._executor.spin, name="kian_ros_terminal_executor", daemon=True
            )
            self._thread.start()
            self._landing_monitor_task = asyncio.create_task(self._poll_landing())
            if self.coordinator is not None:
                self.coordinator.start()
            self._started = True
        except Exception as exc:
            logger.error("RosTerminalPlugin: ROS startup failed: %s", exc, exc_info=True)

    async def stop(self) -> None:
        if self.coordinator is not None:
            await self.coordinator.stop()
        if self._landing_monitor_task is not None:
            self._landing_monitor_task.cancel()
            self._landing_monitor_task = None
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        self._started = False

    async def _poll_landing(self) -> None:
        """后台轮询各机 pending landing,避免在 ROS 回调线程中跑 A* 规划。"""
        while self._started:
            try:
                for planner in self.planners.values():
                    goal = planner.poll_pending_landing()
                    if goal is not None:
                        planner.trigger_landing(goal)
            except Exception as exc:
                logger.error("RosTerminalPlugin: poll_landing error: %s", exc)
            await asyncio.sleep(0.1)

    async def dispatch_selected_goal(self, goal_type: int, drone_key: Optional[str] = None) -> str:
        key = self._resolve_key(drone_key)
        planner = self.planners.get(key)
        if planner is None:
            raise RuntimeError("全局规划器未初始化")
        selected = self.store.latest(key)
        if selected is None:
            raise RuntimeError("尚未在 Web 地图框选目标")
        if selected.get("source") == "vision_qr":
            raise RuntimeError(
                "当前目标点是扫码得到的放物点，不是框选目标；请先在地图上重新框选"
            )
        result = planner.dispatch_selected(selected, goal_type)
        return json.dumps(result, ensure_ascii=False)

    async def planner_status(self, drone_key: Optional[str] = None) -> str:
        if drone_key is not None:
            key = self._resolve_key(drone_key)
            planner = self.planners.get(key)
            status = planner.status() if planner is not None else {"enabled": False, "available": False}
            status["selected_goal"] = self.store.latest(key)
            return json.dumps(status, ensure_ascii=False)
        # 不指定 → 返回全部
        all_status = {}
        for key, planner in self.planners.items():
            st = planner.status()
            st["selected_goal"] = self.store.latest(key)
            all_status[key] = st
        return json.dumps(all_status, ensure_ascii=False)

    def _on_mission_complete(self, drone_key: str, result: dict) -> None:
        if self.app:
            goal = result.get("goal", {})
            label = next((c.label for c in self._drone_configs if c.key == drone_key), drone_key)
            self.app.trigger_proactive_response(
                f"{label}放物任务已完成，已自动规划返航降落。"
                f"降落点 ({goal.get('x', 0):.1f}, {goal.get('y', 0):.1f})，"
                f"共 {result.get('waypoints', 0)} 个航点。"
            )


_plugin: Optional[RosTerminalPlugin] = None


def get_ros_terminal_plugin() -> RosTerminalPlugin:
    global _plugin
    if _plugin is None:
        _plugin = RosTerminalPlugin()
    return _plugin
