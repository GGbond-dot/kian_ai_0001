import asyncio
from typing import Any, Optional

from src.constants.constants import AbortReason, DeviceState
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class UIPlugin(Plugin):
    """UI 插件 - 管理 CLI/GUI 显示"""

    name = "ui"
    priority = 60  # UI 需要在其他插件完成后初始化

    # 设备状态文本映射
    STATE_TEXT_MAP = {
        DeviceState.IDLE: "待命",
        DeviceState.LISTENING: "聆听中...",
        DeviceState.SPEAKING: "说话中...",
    }

    def __init__(self, mode: Optional[str] = None) -> None:
        super().__init__()
        self.app = None
        self.mode = (mode or "cli").lower()
        self.display = None
        self._is_gui = False
        self.is_first = True

    async def setup(self, app: Any) -> None:
        """
        初始化 UI 插件.
        """
        self.app = app

        # 创建对应的 display 实例
        self.display = self._create_display()

        # 禁用应用内控制台输入
        if hasattr(app, "use_console_input"):
            app.use_console_input = False

    def _create_display(self):
        """
        根据模式创建 display 实例.
        """
        if self.mode == "gui":
            from src.display.gui_display import GuiDisplay

            self._is_gui = True
            return GuiDisplay()
        elif self.mode == "web":
            from src.display.web_display import WebDisplay

            self._is_gui = False
            return WebDisplay()
        else:
            from src.display.cli_display import CliDisplay

            self._is_gui = False
            return CliDisplay()

    async def start(self) -> None:
        """
        启动 UI 显示.
        """
        if not self.display:
            return

        # 绑定回调
        await self._setup_callbacks()

        # 启动显示
        self.app.spawn(self.display.start(), name=f"ui:{self.mode}:start")

    async def _setup_callbacks(self) -> None:
        """
        设置 display 回调.
        """
        if self._is_gui or self.mode == "web":
            # GUI / Web：UI 提供按住说话按钮，需要 press/release 回调
            callbacks = {
                "press_callback": self._wrap_callback(self._press),
                "release_callback": self._wrap_callback(self._release),
                "auto_callback": self._wrap_callback(self._auto_toggle),
                "abort_callback": self._wrap_callback(self._abort),
                "send_text_callback": self._send_text,
            }
        else:
            # CLI 直接传递协程函数
            callbacks = {
                "auto_callback": self._auto_toggle,
                "abort_callback": self._abort,
                "send_text_callback": self._send_text,
            }

        await self.display.set_callbacks(**callbacks)

        # 平板 WebView 推上来的外部 PCM —— 仅 WebDisplay 支持
        if hasattr(self.display, "set_audio_in_callback"):
            self.display.set_audio_in_callback(self._on_external_pcm)

        # 把 TTS 远端播放接口注册给 protocol
        protocol = getattr(self.app, "protocol", None)
        if (
            protocol is not None
            and hasattr(self.display, "broadcast_audio_out")
            and hasattr(self.display, "has_audio_out_listeners")
            and hasattr(protocol, "set_tts_remote_sink")
        ):
            protocol.set_tts_remote_sink(
                self.display.broadcast_audio_out,
                self.display.has_audio_out_listeners,
            )

        # 平板直连 TTS：JSON 下行 + 上行回调
        if (
            protocol is not None
            and hasattr(self.display, "broadcast_audio_out_text")
            and hasattr(protocol, "set_tts_remote_text_sink")
        ):
            protocol.set_tts_remote_text_sink(self.display.broadcast_audio_out_text)
        if (
            protocol is not None
            and hasattr(self.display, "set_audio_out_text_callback")
            and hasattr(protocol, "on_tablet_audio_out_text")
        ):
            self.display.set_audio_out_text_callback(protocol.on_tablet_audio_out_text)

    async def _on_external_pcm(self, pcm_bytes: bytes, meta: dict) -> None:
        """把 /ws/audio_in 收到的 PCM 喂给当前 protocol。"""
        protocol = getattr(self.app, "protocol", None)
        if protocol is None:
            return
        feed = getattr(protocol, "feed_external_pcm", None)
        if feed is None:
            return
        try:
            res = feed(pcm_bytes)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            logger.error("feed_external_pcm 失败: %s", e)

    def _wrap_callback(self, coro_func):
        """
        包装协程函数为可调度的 lambda.
        """
        return lambda: self.app.spawn(coro_func(), name="ui:callback")

    async def on_incoming_json(self, message: Any) -> None:
        """
        处理传入的 JSON 消息.
        """
        if not self.display or not isinstance(message, dict):
            return

        msg_type = message.get("type")

        # tts/stt 都更新文本
        if msg_type in ("tts", "stt"):
            if text := message.get("text"):
                await self.display.update_text(text)

        # llm 更新表情
        elif msg_type == "llm":
            if emotion := message.get("emotion"):
                await self.display.update_emotion(emotion)

    async def on_device_state_changed(self, state: Any) -> None:
        """
        设备状态变化处理.
        """
        if not self.display:
            return

        # 跳过首次调用
        if self.is_first:
            self.is_first = False
            return

        # 更新表情和状态
        await self.display.update_emotion("neutral")
        if status_text := self.STATE_TEXT_MAP.get(state):
            await self.display.update_status(status_text, True)

    async def shutdown(self) -> None:
        """
        清理 UI 资源，关闭窗口.
        """
        if self.display:
            await self.display.close()
            self.display = None

    # ===== 回调函数 =====

    async def _send_text(self, text: str):
        """
        发送文本到服务端.
        """
        if self.app.device_state == DeviceState.SPEAKING:
            audio_plugin = self.app.plugins.get_plugin("audio")
            codec = getattr(audio_plugin, "codec", None) if audio_plugin else None
            if codec is not None:
                await codec.clear_audio_queue()
            await self.app.abort_speaking(None)
        if await self.app.connect_protocol():
            await self.app.protocol.send_wake_word_detected(text)

    async def _press(self):
        """
        手动模式：按下开始录音.
        """
        await self.app.start_listening_manual()

    async def _release(self):
        """
        手动模式：释放停止录音.
        """
        await self.app.stop_listening_manual()

    async def _auto_toggle(self):
        """
        自动模式切换.
        """
        await self.app.start_auto_conversation()

    async def _abort(self):
        """
        中断对话.
        """
        await self.app.abort_speaking(AbortReason.USER_INTERRUPTION)
