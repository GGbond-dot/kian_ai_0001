"""ROS 2 subscriber for drone camera + publishers for vision/result and dual_validator/verified."""
from __future__ import annotations

import threading
from typing import Optional

import cv2
import numpy as np

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class VisionBridge:
    def __init__(self, topic: str = "/camera/image_raw/compressed") -> None:
        self.topic = topic
        self._sub = None
        self._result_pub = None
        self._verified_pub = None
        self._node = None
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def attach_ros(self, node) -> None:
        from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import CompressedImage
        from std_msgs.msg import Int8

        from drone_task_interfaces.msg import VisionDetectResult

        self._node = node

        # Subscriber QoS: BEST_EFFORT, KEEP_LAST, depth=1 (matching vision_server.py)
        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._sub = node.create_subscription(
            CompressedImage, self.topic, self._on_image, image_qos
        )

        # Publisher QoS: BEST_EFFORT, KEEP_LAST, depth=5 (matching vision_server.py line 150-153)
        result_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self._result_pub = node.create_publisher(
            VisionDetectResult, 'vision/result', result_qos
        )
        self._verified_pub = node.create_publisher(
            Int8, 'dual_validator/verified', result_qos
        )

        self._available = True
        logger.info("VisionBridge: subscribed to %s, publishing vision/result + dual_validator/verified", self.topic)

    def detach(self) -> None:
        if self._sub is not None and self._node is not None:
            self._node.destroy_subscription(self._sub)
        self._sub = None
        self._result_pub = None
        self._verified_pub = None
        self._node = None
        self._available = False

    def _on_image(self, msg) -> None:
        jpeg_bytes = bytes(msg.data)
        with self._lock:
            self._latest_jpeg = jpeg_bytes

    def pop_latest_frame(self) -> Optional["np.ndarray"]:
        """取出并清空最新帧（参照 vision_server.py line 207-211，避免重复处理同一帧）。"""
        with self._lock:
            jpeg = self._latest_jpeg
            self._latest_jpeg = None
        if jpeg is None:
            return None
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        return frame if frame is not None else None

    def publish_detection(self, msg) -> None:
        if self._result_pub is not None:
            self._result_pub.publish(msg)

    def publish_verified(self, value: int) -> None:
        from std_msgs.msg import Int8

        if self._verified_pub is not None:
            verified = Int8()
            verified.data = value
            self._verified_pub.publish(verified)
