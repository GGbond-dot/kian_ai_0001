"""
VL camera implementation using Zhipu AI.
"""

import base64

import cv2
from openai import OpenAI

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

from .base_camera import BaseCamera

logger = get_logger(__name__)


class VLCamera(BaseCamera):
    """
    智普AI摄像头实现.
    """

    _instance = None

    def __init__(self):
        """
        初始化智普AI摄像头.
        """
        super().__init__()
        config = ConfigManager.get_instance()

        # 初始化OpenAI客户端
        self.client = OpenAI(
            api_key=config.get_config("CAMERA.VLapi_key"),
            base_url=config.get_config(
                "CAMERA.Local_VL_url",
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            ),
        )
        self.model = config.get_config("CAMERA.models", "glm-4v-plus")
        logger.info(f"VL Camera initialized with model: {self.model}")

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
        调用 VLM API（兼容千问/GLM 等 OpenAI 格式）分析图像.
        """
        import json as _json

        try:
            if not self.jpeg_data["buf"]:
                return _json.dumps({"success": False, "message": "Camera buffer is empty"})

            # 将图像转换为 Base64
            image_base64 = base64.b64encode(self.jpeg_data["buf"]).decode("utf-8")

            prompt_text = question if question else "图中描绘的是什么景象？请详细描述。"

            # 准备消息（OpenAI vision 格式，千问/GLM 均兼容）
            messages = [
                {"role": "system", "content": "你是一个图像描述助手，只负责描述画面内容，回答简洁准确。"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                },
            ]

            # 非流式调用（千问/GLM 均支持，稳定性更好）
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
            )

            result = completion.choices[0].message.content or ""
            logger.info(f"VLM 分析完成，question={question[:30]}")
            return _json.dumps({"success": True, "text": result}, ensure_ascii=False)

        except Exception as e:
            error_msg = f"VLM 调用失败: {str(e)}"
            logger.error(error_msg)
            return _json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)
