"""No-fly zone bridge scaffold.

P2 框架: Web 前端先把禁飞区提交到后端,这里保留 ROS 下发边界。
真正 publish 前需要和飞控确认 /a/no_fly_zones 的消息类型与字段。
"""
from __future__ import annotations

from typing import Any, Optional

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

NOFLY_ZONE_TOPIC = "/a/no_fly_zones"


class NoFlyZoneBridge:
    def __init__(self, topic: str = NOFLY_ZONE_TOPIC):
        self.topic = topic
        self._latest_payload: Optional[dict[str, Any]] = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        """启动占位 bridge。

        当前不创建 ROS publisher,避免在飞控消息类型未确认前向
        /a/no_fly_zones 发布错误类型。确认 msg 后在这里接入 rclpy publisher。
        """
        self._available = False
        logger.info(
            "NoFlyZoneBridge: P2 scaffold ready, ROS publish disabled until msg type is confirmed topic=%s",
            self.topic,
        )

    async def stop(self) -> None:
        self._available = False

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收后端校验后的禁飞区 payload。"""
        self._latest_payload = payload
        logger.info(
            "NoFlyZoneBridge: received %d no-fly zones, ROS publish pending msg contract",
            payload.get("count", len(payload.get("zones", []))),
        )
        return {
            "configured": True,
            "published": False,
            "topic": self.topic,
            "reason": "ros_message_contract_pending",
        }

    def latest_payload(self) -> Optional[dict[str, Any]]:
        return self._latest_payload


_bridge: Optional[NoFlyZoneBridge] = None


def get_nofly_zone_bridge() -> NoFlyZoneBridge:
    global _bridge
    if _bridge is None:
        _bridge = NoFlyZoneBridge()
    return _bridge
