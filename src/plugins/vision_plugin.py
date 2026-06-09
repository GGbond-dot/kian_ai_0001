"""Vision plugin: subscribes to drone camera, runs YOLO+QR, publishes vision/result + verified.

Frame-driven detection matching vision_server.py process_callback() +
dual_validator.py validate_callback() logic.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Optional

from src.plugins.base import Plugin
from src.utils.logging_config import get_logger
from src.vision.detection_store import get_detection_store
from src.vision.detector import VisionDetector, draw_overlay
from src.vision.goods_database import GoodsDatabase

import cv2

logger = get_logger(__name__)


class VisionPlugin(Plugin):
    name = "vision"
    priority = 18

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.detector: Optional[VisionDetector] = None
        self.db: Optional[GoodsDatabase] = None
        self.store = get_detection_store()
        self._bridge = None
        self._node = None
        self._executor = None
        self._thread: Optional[threading.Thread] = None
        self._detect_task: Optional[asyncio.Task] = None
        self._enabled = False
        self._last_triggered_qr: str = ""

    async def setup(self, app: Any) -> None:
        self.app = app
        config = app.config.get_config("VISION", {}) or {}
        if not bool(config.get("enabled", False)):
            logger.info("VisionPlugin: disabled by config")
            return

        model_path = str(config.get(
            "model_path", "models/yolo_best_openvino/best.xml"
        ))
        if not Path(model_path).is_absolute():
            model_path = str(Path(__file__).resolve().parents[2] / model_path)

        try:
            self.detector = VisionDetector(
                model_path=model_path,
                device=str(config.get("device", "CPU")),
                conf_threshold=float(config.get("confidence_threshold", 0.3)),
                enable_qr=bool(config.get("enable_qr", True)),
            )
        except Exception as exc:
            logger.error("VisionPlugin: failed to load YOLO model: %s", exc)
            return

        db_path = str(config.get("goods_db_path", "config/goods_location.yaml"))
        if not Path(db_path).is_absolute():
            db_path = str(Path(__file__).resolve().parents[2] / db_path)
        self.db = GoodsDatabase(db_path)

        self._camera_topic = str(
            config.get("camera_topic", "/camera/image_raw/compressed")
        )
        self._enabled = True
        logger.info(
            "VisionPlugin: setup complete (model=%s, goods=%d, topic=%s)",
            model_path, self.db.count, self._camera_topic,
        )

    async def start(self) -> None:
        if not self._enabled:
            return
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node

            from src.ros.vision_bridge import VisionBridge

            if not rclpy.ok():
                rclpy.init(args=None)
            ns = str(self.app.config.get_config("GLOBAL_PLANNER.namespace", "a"))
            self._node = Node("kian_vision", namespace=ns)
            self._bridge = VisionBridge(self._camera_topic)
            self._bridge.attach_ros(self._node)
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)
            self._thread = threading.Thread(
                target=self._executor.spin,
                name="kian_vision_executor",
                daemon=True,
            )
            self._thread.start()

            self._detect_task = asyncio.create_task(self._detection_loop())
            self._started = True
            logger.info("VisionPlugin: started, ROS node up")
        except Exception as exc:
            logger.error("VisionPlugin: ROS startup failed: %s", exc, exc_info=True)

    async def stop(self) -> None:
        if self._detect_task is not None:
            self._detect_task.cancel()
            self._detect_task = None
        if self._bridge is not None:
            self._bridge.detach()
            self._bridge = None
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        self._started = False
        logger.info("VisionPlugin: stopped")

    async def _detection_loop(self) -> None:
        """帧驱动检测循环，参照 vision_server.py process_callback() +
        dual_validator.py validate_callback()。

        快速轮询 pop_latest_frame()（取出即清空，避免堆积），有新帧就检测。
        实际处理速率 = 相机帧率。
        """
        from drone_task_interfaces.msg import VisionDetectResult

        logger.info("VisionPlugin: detection loop started (frame-driven)")
        while self._started:
            frame = self._bridge.pop_latest_frame()
            if frame is None:
                await asyncio.sleep(0.005)
                continue

            result = self.detector.detect(frame)

            # ── 1. 每帧发布 vision/result（参照 vision_server.py line 264）──
            ros_result = VisionDetectResult()
            ros_result.header.stamp = self._node.get_clock().now().to_msg()
            ros_result.header.frame_id = "camera"
            ros_result.detected = result.detected
            ros_result.qr_detected = result.qr_detected
            ros_result.qr_data = result.qr_data
            ros_result.bbox_center_x = result.bbox_center_x
            ros_result.bbox_center_y = result.bbox_center_y
            ros_result.bbox_width = result.bbox_width
            ros_result.bbox_height = result.bbox_height
            ros_result.confidence = result.confidence
            ros_result.class_id = result.class_id
            self._bridge.publish_detection(ros_result)

            # ── 2. 写入 DetectionStore（每帧，含 verified 状态）──
            goods = None
            if result.qr_detected and result.qr_data and self.db:
                goods = self.db.lookup(result.qr_data)
            self.store.submit({
                "detected": result.detected,
                "qr_detected": result.qr_detected,
                "qr_data": result.qr_data,
                "verified": result.qr_detected and goods is not None,
                "bbox_center_x": result.bbox_center_x,
                "bbox_center_y": result.bbox_center_y,
                "goods_name": goods.name if goods else "",
                "place_x": goods.place_x if goods else 0.0,
                "place_y": goods.place_y if goods else 0.0,
                "place_z": goods.place_z if goods else 0.5,
            })

            # ── 3. 发布 dual_validator/verified（参照 dual_validator.py line 62-105）──
            yolo_ok = result.detected
            qr_ok = result.qr_detected

            if yolo_ok or qr_ok:
                if qr_ok and yolo_ok:
                    val = 3
                elif qr_ok:
                    val = 2
                else:
                    val = 1

                # QR 不在 goods DB → 降级（dual_validator.py line 88-95）
                if qr_ok and self.db and goods is None:
                    if yolo_ok:
                        val = 1
                    else:
                        val = 0

                if val > 0:
                    self._bridge.publish_verified(val)

                # ── 4. QR 验证通过 → 预规划放物点（同 QR 码只触发一次）──
                if val >= 2 and goods and self._last_triggered_qr != result.qr_data:
                    self._last_triggered_qr = result.qr_data
                    await self._pre_plan_place(goods)
                    if self.app:
                        self.app.trigger_proactive_response(
                            f"视觉系统：已扫描到货物 {goods.name}（QR: {result.qr_data}），"
                            f"放物坐标已预规划 ({goods.place_x:.1f}, {goods.place_y:.1f}, z={goods.place_z:.1f})"
                        )

            # ── 5. 推送标注视频帧到前端 ──
            if self.app is not None:
                try:
                    disp = frame.copy()
                    matched = bool(result.qr_data and goods is not None)
                    draw_overlay(disp, result.detections, result.qr_data,
                                 result.qr_corners, matched, result.fps)
                    _, jpeg = cv2.imencode('.jpg', disp, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    self.app.broadcast_video_frame(jpeg.tobytes())
                except Exception:
                    pass

            # end while

    async def _pre_plan_place(self, goods) -> None:
        """将放物坐标存入 goal_selection_store，供后续 vision.dispatch_place 使用。"""
        from src.ros.goal_selection_store import get_goal_selection_store
        store = get_goal_selection_store()
        await store.submit({
            "cx": goods.place_x,
            "cy": goods.place_y,
            "z": goods.place_z,
        })

    async def get_detection(self) -> dict[str, Any]:
        return self.store.latest() or {
            "detected": False,
            "qr_detected": False,
            "qr_data": "",
            "verified": False,
            "goods_name": "",
            "bbox_center_x": 0.0,
            "bbox_center_y": 0.0,
            "place_x": 0.0,
            "place_y": 0.0,
            "place_z": 0.5,
        }

    async def dispatch_place(self) -> str:
        import json

        from src.ros.goal_selection_store import get_goal_selection_store

        goal_store = get_goal_selection_store()
        selected = goal_store.latest()
        if selected is None:
            raise RuntimeError("未检测到放物点，请先确认货物 QR 码已验证")

        from src.plugins.ros_terminal import get_ros_terminal_plugin

        ros_plugin = get_ros_terminal_plugin()
        if ros_plugin.planner is None:
            raise RuntimeError("全局规划器未初始化")

        result = ros_plugin.planner.dispatch_selected(selected, goal_type=2)
        self.store.clear()
        return json.dumps(result, ensure_ascii=False)

    @property
    def ready(self) -> bool:
        return self._started and self.detector is not None


_plugin: Optional[VisionPlugin] = None


def get_vision_plugin() -> VisionPlugin:
    global _plugin
    if _plugin is None:
        _plugin = VisionPlugin()
    return _plugin
