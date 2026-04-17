"""
SceneMonitor — 后台定时拍照 + VLM 分析，缓存最新场景描述。

设计：
  - 单例，asyncio 后台 Task
  - 每隔 interval 秒：capture() → analyze() → 更新 _latest_description
  - get_latest() 供 MCP 工具直接返回给 LLM
"""
import asyncio
import json
import time

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 发给 VLM 的固定 prompt（只让它描述场景，不做其他事）
_SCENE_PROMPT = (
    "请简洁描述当前画面：有几个人、他们在做什么、整体场景如何。"
    "用中文回答，不超过60字。"
)


class SceneMonitor:
    """定时拍照并调用 VLM 分析，缓存最新场景描述供 LLM 查询。"""

    _instance = None

    def __init__(self):
        self._interval: int = 30
        self._task: asyncio.Task = None
        self._latest_description: str = ""
        self._latest_time: float = 0.0
        self._running: bool = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SceneMonitor()
        return cls._instance

    # ------------------------------------------------------------------
    def start(self, interval: int = 30):
        """启动后台监控（必须在 asyncio 事件循环中调用）。"""
        if self._running:
            logger.info("SceneMonitor 已在运行，跳过重复启动")
            return
        config = ConfigManager.get_instance()
        # 允许 config 覆盖 interval
        interval = int(config.get_config("CAMERA.scene_monitor_interval", interval))
        self._interval = interval
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"SceneMonitor 已启动，拍照间隔 {interval}s")

    def stop(self):
        """停止后台监控。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("SceneMonitor 已停止")

    def get_latest(self) -> dict:
        """返回最新场景描述（立即返回缓存，不阻塞）。"""
        if not self._latest_description:
            return {
                "available": False,
                "message": "暂无场景数据，后台正在首次分析中",
            }
        age = int(time.time() - self._latest_time)
        captured_at = time.strftime("%H:%M:%S", time.localtime(self._latest_time))
        return {
            "available": True,
            "description": self._latest_description,
            "age_seconds": age,
            "captured_at": captured_at,
        }

    # ------------------------------------------------------------------
    async def _loop(self):
        """后台循环：拍照 → 调 VLM → 缓存结果。"""
        # 延迟导入避免循环依赖
        from src.mcp.tools.camera import get_camera_instance

        while self._running:
            try:
                camera = get_camera_instance()

                # capture() 是 cv2 阻塞调用，放到线程池
                success = await asyncio.to_thread(camera.capture)
                if not success:
                    logger.warning("SceneMonitor: 拍照失败，跳过本次 VLM 分析")
                else:
                    # analyze() 是同步网络请求，同样放到线程池
                    result_json = await asyncio.to_thread(
                        camera.analyze, _SCENE_PROMPT
                    )
                    try:
                        data = json.loads(result_json)
                        if data.get("success"):
                            self._latest_description = data.get("text", "")
                            self._latest_time = time.time()
                            logger.info(
                                f"SceneMonitor 场景更新: {self._latest_description[:60]}"
                            )
                        else:
                            logger.warning(
                                f"SceneMonitor VLM 返回失败: {data.get('message')}"
                            )
                    except json.JSONDecodeError:
                        logger.error("SceneMonitor: VLM 返回无法解析为 JSON")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SceneMonitor 循环异常: {e}", exc_info=True)

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
