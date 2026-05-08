from typing import Any, Optional

from src.mcp.mcp_server import McpServer
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class McpPlugin(Plugin):
    name = "mcp"
    priority = 20  # 工具注册，需要较早初始化

    def __init__(self) -> None:
        super().__init__()
        self.app: Any = None
        self._server: Optional[McpServer] = None

    async def setup(self, app: Any) -> None:
        self.app = app
        self._server = McpServer.get_instance()

        # 通过应用协议发送MCP响应
        async def _send(msg: str):
            try:
                if not self.app or not getattr(self.app, "protocol", None):
                    return
                await self.app.protocol.send_mcp_message(msg)
            except Exception:
                pass

        try:
            self._server.set_send_callback(_send)
            # 注册通用工具（包含 calendar 工具）。提醒服务的运行改由 CalendarPlugin 管理
            self._server.add_common_tools()
        except Exception:
            pass

        # SceneMonitor（后台定时拍照+VLM 分析）已下线 —— 当前部署无可用摄像头，
        # 之前每 30s 报一次"拍照失败"刷屏。需要重启时改回原代码。

    async def on_incoming_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        try:
            # 处理 MCP 消息
            if message.get("type") == "mcp":
                payload = message.get("payload")
                if not payload:
                    return
                if self._server is None:
                    self._server = McpServer.get_instance()
                await self._server.parse_message(payload)
        except Exception:
            pass

    async def shutdown(self) -> None:
        # SceneMonitor 已下线，无需 stop
        # 可选：解除回调引用，帮助GC
        try:
            if self._server:
                self._server.set_send_callback(None)  # type: ignore[arg-type]
        except Exception:
            pass
