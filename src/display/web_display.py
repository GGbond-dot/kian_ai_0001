"""
Web 显示模块 — 通过 WebSocket 将 UI 状态推送到远程浏览器.

实现 BaseDisplay 接口，与 GuiDisplay / CliDisplay 平级。
"""

import asyncio
from typing import Callable, Optional

from src.display.base_display import BaseDisplay
from src.display.screen_kiosk import ScreenKiosk
from src.display.slam_bridge import SlamBridge
from src.display.slam_bridge import encode_points
from src.display import slam_constants as C
from src.display.web_server import WebServer
from src.ros.drone_command_bridge import get_all_drone_command_bridges, get_drone_command_bridge
from src.ros.drone_config import load_drone_configs
from src.ros.grasp_task_bridge import get_grasp_task_bridge
from src.ros.nofly_zone_bridge import get_nofly_zone_bridge
import numpy as np


class WebDisplay(BaseDisplay):
    """Web 显示类 — 基于 FastAPI + WebSocket 的远程 UI."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        super().__init__()
        self.server = WebServer(host, port)
        self.screen_kiosk = ScreenKiosk(port)
        self.slam_bridge = SlamBridge(self.server)
        # 为每架机的 command_topic 各建一个 bridge(单机回退时即默认 /drone_command)
        from src.utils.config_manager import ConfigManager
        _drone_cfgs = load_drone_configs(ConfigManager.get_instance())
        # 机号 → 0基序号(规划路径分 channel 用)
        self._drone_index = {c.key: i for i, c in enumerate(_drone_cfgs)}
        for cfg in _drone_cfgs:
            if cfg.enabled:
                get_drone_command_bridge(cfg.command_topic)
        # 兼容属性:默认 /drone_command bridge(旧引用)
        self.drone_bridge = get_drone_command_bridge()
        self.nofly_bridge = get_nofly_zone_bridge()
        self.grasp_bridge = get_grasp_task_bridge()

        # 自动模式状态
        self.auto_mode = False

        # 回调
        self._callbacks = {
            "button_press": None,
            "button_release": None,
            "mode": None,
            "auto": None,
            "abort": None,
            "send_text": None,
        }

        # 注册控制指令处理
        self.server.set_command_callback(self._handle_command)
        self.server.set_nofly_zone_callback(self.nofly_bridge.submit)
        self.server.set_grasp_task_callback(self.grasp_bridge.submit)
        self.server.set_camera_enable_callback(self._set_camera_enable)

    async def _set_camera_enable(self, enable: bool) -> bool:
        """前端浮窗按钮 → /api/camera_enable → 这里 → 视觉插件控制相机推流。"""
        from src.plugins.vision_plugin import get_vision_plugin
        try:
            return await get_vision_plugin().set_camera_stream(enable)
        except RuntimeError as exc:
            self.logger.warning("camera_enable: %s", exc)
            return False

    def set_audio_in_callback(self, callback: Callable) -> None:
        """注册外部 PCM 流回调（来自平板 WebView 的 /ws/audio_in）。

        callback 签名: async (pcm_bytes: bytes, meta: dict) -> None
        """
        self.server.set_audio_in_callback(callback)

    def has_audio_out_listeners(self) -> bool:
        return self.server.has_audio_out_listeners()

    async def broadcast_audio_out(self, mp3_bytes: bytes) -> int:
        return await self.server.broadcast_audio_out(mp3_bytes)

    async def broadcast_audio_out_text(self, msg: dict) -> int:
        return await self.server.broadcast_audio_out_text(msg)

    async def broadcast_planned_path(self, points, planning_z: float, drone_key=None) -> None:
        xyz = np.asarray([(x, y, planning_z) for x, y in points], dtype=np.float32)
        idx = self._drone_index.get(drone_key, 0) if drone_key else 0
        channel = C.CHAN_PATH if idx == 0 else (C.CHAN_PATH_DRONE_BASE + idx)
        await self.server.broadcast_slam_bytes(encode_points(channel, xyz))

    def set_audio_out_text_callback(self, callback: Callable) -> None:
        self.server.set_audio_out_text_callback(callback)

    async def set_callbacks(
        self,
        press_callback: Optional[Callable] = None,
        release_callback: Optional[Callable] = None,
        mode_callback: Optional[Callable] = None,
        auto_callback: Optional[Callable] = None,
        abort_callback: Optional[Callable] = None,
        send_text_callback: Optional[Callable] = None,
    ):
        self._callbacks.update({
            "button_press": press_callback,
            "button_release": release_callback,
            "mode": mode_callback,
            "auto": auto_callback,
            "abort": abort_callback,
            "send_text": send_text_callback,
        })

    async def update_status(self, status: str, connected: bool):
        self.server.update_state("status", status)
        self.server.update_state("connected", connected)
        await self.server.broadcast({
            "type": "status",
            "status": status,
            "connected": connected,
        })

    async def update_text(self, text: str):
        self.server.update_state("text", text)
        await self.server.broadcast({"type": "text", "text": text})

    async def update_emotion(self, emotion_name: str):
        self.server.update_state("emotion", emotion_name)
        await self.server.broadcast({"type": "emotion", "emotion": emotion_name})

    async def update_button_status(self, text: str):
        self.server.update_state("button_text", text)
        await self.server.broadcast({"type": "button", "text": text})

    async def update_video_frame(self, jpeg_bytes: bytes) -> None:
        await self.server.broadcast_video_frame(jpeg_bytes)

    async def start(self):
        """启动 Web 服务器 (会阻塞直到服务器关闭)."""
        self.logger.info("WebDisplay 启动中...")
        await self.slam_bridge.start()
        for bridge in get_all_drone_command_bridges():
            await bridge.start()
        await self.nofly_bridge.start()
        await self.grasp_bridge.start()
        # 板上随机屏幕:等端口就绪后自动拉起 kiosk 浏览器(SCREEN_PANEL.ENABLED)
        self.screen_kiosk.start()
        await self.server.start()

    async def close(self):
        """关闭 Web 服务器."""
        self.logger.info("WebDisplay 关闭中...")
        await self.screen_kiosk.stop()
        for bridge in get_all_drone_command_bridges():
            await bridge.stop()
        await self.nofly_bridge.stop()
        await self.grasp_bridge.stop()
        await self.slam_bridge.stop()
        await self.server.stop()

    # ===================== 控制指令处理 =====================

    async def _handle_command(self, action: str, data: dict) -> None:
        """处理来自浏览器的控制指令."""

        if action == "press":
            if self._callbacks["button_press"]:
                await self._call(self._callbacks["button_press"])

        elif action == "release":
            if self._callbacks["button_release"]:
                await self._call(self._callbacks["button_release"])

        elif action == "auto":
            if self._callbacks["auto"]:
                await self._call(self._callbacks["auto"])

        elif action == "abort":
            if self._callbacks["abort"]:
                await self._call(self._callbacks["abort"])

        elif action == "send_text":
            text = data.get("text", "").strip()
            if text and self._callbacks["send_text"]:
                await self._callbacks["send_text"](text)

        elif action == "mode":
            # 模式切换: 与 GuiDisplay 逻辑一致
            self.auto_mode = not self.auto_mode
            mode_text = "自动对话" if self.auto_mode else "手动对话"
            self.server.update_state("auto_mode", self.auto_mode)
            await self.server.broadcast({
                "type": "auto_mode",
                "value": self.auto_mode,
            })
            if self._callbacks["mode"]:
                await self._call(self._callbacks["mode"])

    async def _call(self, callback: Callable) -> None:
        """安全调用回调 (支持协程和普通函数)."""
        try:
            result = callback()
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            self.logger.error("回调执行失败: %s", e)
