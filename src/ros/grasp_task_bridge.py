"""Compatibility bridge for Web map goal selection.

Web selection only stores the center point. MCP dispatch explicitly starts planning.
"""
from __future__ import annotations

from typing import Any, Optional

from src.utils.logging_config import get_logger
from src.ros.goal_selection_store import get_goal_selection_store

logger = get_logger(__name__)

GOAL_WITH_TYPE_TOPIC = "/goal_with_type"
GRASP_GOAL_Z = 0.5
GRASP_GOAL_TYPE_PICKUP = 1          # GoalWithType.goal_type = 1 (PICKUP)
GRASP_DEFAULT_INTERRUPT_MODE = 0


class GraspTaskBridge:
    def __init__(self, topic: str = GOAL_WITH_TYPE_TOPIC):
        self.topic = topic
        self._latest_task: Optional[dict[str, Any]] = None
        self._available = True
        self._store = get_goal_selection_store()

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        self._available = True
        logger.info("GraspTaskBridge: Web selection store ready")

    async def stop(self) -> None:
        self._available = True

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收后端校验后的抓取任务 payload (含 cx, cy, z, goal_type, interrupt_mode)。"""
        payload = dict(payload)
        payload["interrupt_mode"] = 0
        payload["dwell_time"] = 0.0
        payload["yaw_deg"] = -1.0
        self._latest_task = payload
        result = await self._store.submit(payload)
        logger.info(
            "GraspTaskBridge: stored selected goal cx=%.3f cy=%.3f z=%.3f goal_type=%d interrupt=%d",
            payload.get("cx", 0.0),
            payload.get("cy", 0.0),
            payload.get("z", 0.0),
            payload.get("goal_type", GRASP_GOAL_TYPE_PICKUP),
            payload.get("interrupt_mode", GRASP_DEFAULT_INTERRUPT_MODE),
        )
        return result

    def latest_task(self) -> Optional[dict[str, Any]]:
        return self._latest_task


_bridge: Optional[GraspTaskBridge] = None


def get_grasp_task_bridge() -> GraspTaskBridge:
    global _bridge
    if _bridge is None:
        _bridge = GraspTaskBridge()
    return _bridge
