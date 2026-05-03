"""Drone command publisher bridge — 主进程驻留 publisher 单例。

替代每次 subprocess.Popen 起 ros2_int32_publisher.py 的冷启动方案。
与 SlamBridge 同进程共存,共享 rclpy.init();独立 Node + 独立 SingleThreadedExecutor。

接口语义(参考 background04 决策):
  - publish_command(value, duration=8.0):新命令立即覆盖旧命令(cancel 旧 task),
    立即发一帧,后续每 100ms 发一次,持续 duration 秒
  - 同时只有一个 active publish task
  - 启动失败时 available=False,调用方走 subprocess fallback
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

DRONE_COMMAND_TOPIC = "/drone_command"
PUBLISH_INTERVAL_SEC = 0.1
DEFAULT_DURATION_SEC = 8.0


class DroneCommandBridge:
    def __init__(self, topic: str = DRONE_COMMAND_TOPIC):
        self.topic = topic
        self._node = None
        self._publisher = None
        self._executor = None
        self._exec_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._active_task: Optional[asyncio.Task] = None
        self._uint8_cls = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        if self._available:
            return

        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
            from std_msgs.msg import UInt8
        except ImportError as exc:
            logger.warning(
                "DroneCommandBridge: rclpy 不可用,走 subprocess fallback (%s)", exc
            )
            return

        self._loop = asyncio.get_running_loop()
        self._uint8_cls = UInt8

        try:
            if not rclpy.ok():
                rclpy.init(args=None)
            self._node = Node("drone_command_bridge")
            self._publisher = self._node.create_publisher(UInt8, self.topic, 10)

            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)

            self._exec_thread = threading.Thread(
                target=self._executor.spin,
                name="drone_command_bridge_executor",
                daemon=True,
            )
            self._exec_thread.start()
        except Exception as exc:
            logger.error("DroneCommandBridge: 启动失败 → %s", exc, exc_info=True)
            self._cleanup()
            return

        # Topic discovery 预热:发一帧 0(无意义指令码)触发 publisher 注册 + DDS 发现
        try:
            warmup = UInt8()
            warmup.data = 0
            self._publisher.publish(warmup)
        except Exception as exc:
            logger.warning("DroneCommandBridge: 预热 publish 失败 (%s),继续", exc)

        self._available = True
        logger.info("DroneCommandBridge: 启动成功 topic=%s", self.topic)

    async def stop(self) -> None:
        await self._cancel_active_task()
        self._cleanup()

    async def publish_command(
        self,
        value: int,
        duration: float = DEFAULT_DURATION_SEC,
    ) -> None:
        """发布 UInt8 指令。新命令立即覆盖旧命令(决策 Q1)。"""
        if not self._available or self._publisher is None or self._uint8_cls is None:
            raise RuntimeError("DroneCommandBridge not available")
        if not 0 <= value <= 255:
            raise ValueError(f"UInt8 value must be in [0, 255], got {value}")

        await self._cancel_active_task()

        msg = self._uint8_cls()
        msg.data = int(value)

        try:
            self._publisher.publish(msg)
        except Exception as exc:
            logger.error("DroneCommandBridge: 立即 publish 失败 → %s", exc)
            raise

        self._active_task = asyncio.create_task(
            self._persist_publish(msg, duration),
            name=f"drone_publish:{value}",
        )

    async def _persist_publish(self, msg, duration: float) -> None:
        loop = asyncio.get_event_loop()
        end_t = loop.time() + max(0.0, duration)
        try:
            while True:
                await asyncio.sleep(PUBLISH_INTERVAL_SEC)
                if loop.time() >= end_t:
                    return
                if self._publisher is None:
                    return
                try:
                    self._publisher.publish(msg)
                except Exception as exc:
                    logger.warning(
                        "DroneCommandBridge: persist publish 异常 → %s", exc
                    )
                    return
        except asyncio.CancelledError:
            logger.debug("DroneCommandBridge: persist task 被新命令覆盖")
            raise

    async def _cancel_active_task(self) -> None:
        task = self._active_task
        self._active_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _cleanup(self) -> None:
        self._available = False
        if self._executor is not None:
            try:
                self._executor.shutdown()
            except Exception:
                pass
            self._executor = None
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
        self._publisher = None


_bridge: Optional[DroneCommandBridge] = None


def get_drone_command_bridge() -> DroneCommandBridge:
    """获取进程级单例。第一次调用只构造,真正启动需 await bridge.start()。"""
    global _bridge
    if _bridge is None:
        _bridge = DroneCommandBridge()
    return _bridge
