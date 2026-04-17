"""
Base camera implementation.
"""

import platform
import time
import threading
from abc import ABC, abstractmethod
from typing import Dict

import cv2

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class BaseCamera(ABC):
    """
    基础摄像头类，定义接口.
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        """
        初始化基础摄像头.
        """
        self.jpeg_data = {"buf": b"", "len": 0}  # 图像的JPEG字节数据  # 字节数据长度

        # 从配置中读取相机参数
        config = ConfigManager.get_instance()
        self.camera_index = config.get_config("CAMERA.camera_index", 0)
        self.camera_device = str(config.get_config("CAMERA.camera_device", "") or "").strip()
        self.frame_width = config.get_config("CAMERA.frame_width", 640)
        self.frame_height = config.get_config("CAMERA.frame_height", 480)
        self.fps = config.get_config("CAMERA.fps", 30)

    @abstractmethod
    def capture(self) -> bool:
        """
        捕获图像.
        """

    @abstractmethod
    def analyze(self, question: str) -> str:
        """
        分析图像.
        """

    def get_jpeg_data(self) -> Dict[str, any]:
        """
        获取JPEG数据.
        """
        return self.jpeg_data

    def set_jpeg_data(self, data_bytes: bytes):
        """
        设置JPEG数据.
        """
        self.jpeg_data["buf"] = data_bytes
        self.jpeg_data["len"] = len(data_bytes)

    def _camera_open_candidates(self):
        """
        生成可尝试的摄像头打开方式。
        优先：
        1. 显式 camera_device 路径
        2. Linux 下 /dev/video{camera_index}
        3. 原始 camera_index
        """
        seen = set()
        candidates = []
        is_linux = platform.system().lower() == "linux"

        if self.camera_device:
            candidates.append((self.camera_device, cv2.CAP_V4L2, "camera-device-v4l2"))
            candidates.append((self.camera_device, None, "camera-device"))

        if is_linux and isinstance(self.camera_index, int) and self.camera_index >= 0:
            candidates.append(
                (f"/dev/video{self.camera_index}", cv2.CAP_V4L2, "linux-v4l2-path")
            )
            candidates.append((self.camera_index, cv2.CAP_V4L2, "linux-v4l2-index"))

        candidates.append((self.camera_index, None, "default"))

        for source, backend, label in candidates:
            key = (str(source), backend)
            if key in seen:
                continue
            seen.add(key)
            yield source, backend, label

    def _capture_frame(self):
        """
        捕获单帧图像，优先走 Linux V4L2 + MJPG。
        """
        for source, backend, label in self._camera_open_candidates():
            cap = None
            try:
                logger.info("Opening camera via %s: %s", label, source)
                cap = (
                    cv2.VideoCapture(source)
                    if backend is None
                    else cv2.VideoCapture(source, backend)
                )

                if not cap.isOpened():
                    logger.warning("Cannot open camera via %s: %s", label, source)
                    continue

                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
                if self.fps:
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                if platform.system().lower() == "linux":
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                frame = None
                for attempt in range(6):
                    ret, candidate = cap.read()
                    if ret and candidate is not None:
                        frame = candidate
                        if attempt >= 1:
                            break
                    time.sleep(0.05)

                if frame is not None:
                    logger.info("Camera frame captured via %s", label)
                    return frame

                logger.warning("No frame received via %s", label)
            except Exception as e:
                logger.warning("Camera capture failed via %s: %s", label, e)
            finally:
                if cap is not None:
                    cap.release()

        return None

    def _prepare_jpeg_frame(self, frame) -> bool:
        """
        缩放并编码为 JPEG。
        """
        height, width = frame.shape[:2]
        max_dim = max(height, width)
        scale = 320 / max_dim if max_dim > 320 else 1.0

        if scale < 1.0:
            new_width = int(width * scale)
            new_height = int(height * scale)
            frame = cv2.resize(
                frame, (new_width, new_height), interpolation=cv2.INTER_AREA
            )

        success, jpeg_data = cv2.imencode(".jpg", frame)
        if not success:
            logger.error("Failed to encode image to JPEG")
            return False

        self.set_jpeg_data(jpeg_data.tobytes())
        logger.info(
            "Image captured successfully (size: %s bytes)", self.jpeg_data["len"]
        )
        return True
