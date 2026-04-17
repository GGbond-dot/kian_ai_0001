"""
Normal camera implementation using remote API.
"""

import cv2
import requests

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

from .base_camera import BaseCamera

logger = get_logger(__name__)


class NormalCamera(BaseCamera):
    """
    普通摄像头实现，使用远程API进行分析.
    """

    _instance = None

    def __init__(self):
        """
        初始化普通摄像头.
        """
        super().__init__()
        self.explain_url = ""
        self.explain_token = ""

    @classmethod
    def get_instance(cls):
        """
        获取单例实例.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_explain_url(self, url: str):
        """
        设置解释服务的URL.
        """
        self.explain_url = url
        logger.info(f"Vision service URL set to: {url}")

    def set_explain_token(self, token: str):
        """
        设置解释服务的token.
        """
        self.explain_token = token
        if token:
            logger.info("Vision service token has been set")

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

    def analyze(self, question: str) -> str:
        """
        分析图像.
        """
        if not self.explain_url:
            return '{"success": false, "message": "Image explain URL is not set"}'

        if not self.jpeg_data["buf"]:
            return '{"success": false, "message": "Camera buffer is empty"}'

        # 准备请求头
        headers = {
            "Device-Id": ConfigManager.get_instance().get_config(
                "SYSTEM_OPTIONS.DEVICE_ID"
            ),
            "Client-Id": ConfigManager.get_instance().get_config(
                "SYSTEM_OPTIONS.CLIENT_ID"
            ),
        }

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
