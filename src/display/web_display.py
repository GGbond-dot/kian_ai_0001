"""
Web 显示模块 — 通过 WebSocket 将 UI 状态推送到远程浏览器.

实现 BaseDisplay 接口，与 GuiDisplay / CliDisplay 平级。
"""

import asyncio
from typing import Callable, Optional

from src.display.base_display import BaseDisplay
from src.display.slam_bridge import SlamBridge
from src.display.web_server import WebServer
from src.ros.drone_command_bridge import get_drone_command_bridge


class WebDisplay(BaseDisplay):
    """Web 显示类 — 基于 FastAPI + WebSocket 的远程 UI."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        super().__init__()
        self.server = WebServer(host, port)
        self.slam_bridge = SlamBridge(self.server)
        self.drone_bridge = get_drone_command_bridge()

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

    async def start(self):
        """启动 Web 服务器 (会阻塞直到服务器关闭)."""
        self.logger.info("WebDisplay 启动中...")
        await self.slam_bridge.start()
        await self.drone_bridge.start()
        await self.server.start()

    async def close(self):
        """关闭 Web 服务器."""
        self.logger.info("WebDisplay 关闭中...")
        await self.drone_bridge.stop()
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
