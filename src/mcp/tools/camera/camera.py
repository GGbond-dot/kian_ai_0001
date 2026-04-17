import threading
import time
import platform

import cv2
import requests

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class Camera:
    _instance = None
    _lock = threading.Lock()  # 线程安全

    def __init__(self):
        self.explain_url = ""
        self.explain_token = ""
        self.jpeg_data = {"buf": b"", "len": 0}  # 图像的JPEG字节数据  # 字节数据长度

        # 从配置中读取相机参数
        config = ConfigManager.get_instance()
        self.camera_index = config.get_config("CAMERA.camera_index", 0)
        self.camera_device = str(config.get_config("CAMERA.camera_device", "") or "").strip()
        self.frame_width = config.get_config("CAMERA.frame_width", 640)
        self.frame_height = config.get_config("CAMERA.frame_height", 480)
        self.fps = config.get_config("CAMERA.fps", 30)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_explain_url(self, url):
        """
        设置解释服务的URL.
        """
        self.explain_url = url
        logger.info(f"Vision service URL set to: {url}")

    def set_explain_token(self, token):
        """
        设置解释服务的token.
        """
        self.explain_token = token
        if token:
            logger.info("Vision service token has been set")

    def set_jpeg_data(self, data_bytes):
        """
        设置JPEG图像数据.
        """
        self.jpeg_data["buf"] = data_bytes
        self.jpeg_data["len"] = len(data_bytes)

    def capture(self) -> bool:
        """
        捕获图像.
        """
        try:
            logger.info("Accessing camera...")
            frame = self._capture_frame()
            if frame is None:
                logger.error("Failed to capture image")
                return False
            return self._prepare_jpeg_frame(frame)

        except Exception as e:
            logger.error(f"Exception during capture: {e}")
            return False

    def _camera_open_candidates(self):
        seen = set()
        candidates = []
        is_linux = platform.system().lower() == "linux"

        if self.camera_device:
            candidates.append((self.camera_device, cv2.CAP_V4L2, "camera-device-v4l2"))
            candidates.append((self.camera_device, None, "camera-device"))

        if is_linux and isinstance(self.camera_index, int) and self.camera_index >= 0:
            candidates.append((f"/dev/video{self.camera_index}", cv2.CAP_V4L2, "linux-v4l2-path"))
            candidates.append((self.camera_index, cv2.CAP_V4L2, "linux-v4l2-index"))

        candidates.append((self.camera_index, None, "default"))

        for source, backend, label in candidates:
            key = (str(source), backend)
            if key in seen:
                continue
            seen.add(key)
            yield source, backend, label

    def _capture_frame(self):
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

        self.jpeg_data["buf"] = jpeg_data.tobytes()
        self.jpeg_data["len"] = len(self.jpeg_data["buf"])
        logger.info("Image captured successfully (size: %s bytes)", self.jpeg_data["len"])
        return True

    def get_device_id(self):
        """
        获取设备ID.
        """
        return ConfigManager.get_instance().get_config("SYSTEM_OPTIONS.DEVICE_ID")

    def get_client_id(self):
        """
        获取客户端ID.
        """
        return ConfigManager.get_instance().get_config("SYSTEM_OPTIONS.CLIENT_ID")

    def explain(self, question: str) -> str:
        """
        发送图像分析请求.
        """
        if not self.explain_url:
            return '{"success": false, "message": "Image explain URL is not set"}'

        if not self.jpeg_data["buf"]:
            return '{"success": false, "message": "Camera buffer is empty"}'

        # 准备请求头
        headers = {"Device-Id": self.get_device_id(), "Client-Id": self.get_client_id()}

        if self.explain_token:
            headers["Authorization"] = f"Bearer {self.explain_token}"

        # 准备文件数据
        files = {
            "question": (None, question),
            "file": ("camera.jpg", self.jpeg_data["buf"], "image/jpeg"),
        }

        try:
            # 发送请求
            response = requests.post(
                self.explain_url, headers=headers, files=files, timeout=10
            )

            # 检查响应状态
            if response.status_code != 200:
                error_msg = (
                    f"Failed to upload photo, status code: {response.status_code}"
                )
                logger.error(error_msg)
                return f'{{"success": false, "message": "{error_msg}"}}'

            # 记录响应
            logger.info(
                f"Explain image size={self.jpeg_data['len']}, "
                f"question={question}\n{response.text}"
            )
            return response.text

        except requests.RequestException as e:
            error_msg = f"Failed to connect to explain URL: {str(e)}"
            logger.error(error_msg)
            return f'{{"success": false, "message": "{error_msg}"}}'


def take_photo(arguments: dict) -> str:
    """
    拍照并解释的工具函数.
    """
    camera = Camera.get_instance()
    question = arguments.get("question", "")

    # 拍照
    success = camera.capture()
    if not success:
        return '{"success": false, "message": "Failed to capture photo"}'

    # 发送解释请求
    return camera.explain(question)
