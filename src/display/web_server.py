"""
Web 服务器 — FastAPI + WebSocket 管理.

负责:
  1. 提供静态前端文件 (index.html, app.js, style.css)
  2. 提供表情资源文件 (/emojis/)
  3. WebSocket 连接管理和消息广播
  4. 接收浏览器控制指令并转发给回调
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.utils.logging_config import get_logger
from src.utils.resource_finder import find_assets_dir

logger = get_logger(__name__)

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "web_static"


class WebServer:
    """管理 FastAPI 应用和 WebSocket 连接."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.app = FastAPI(title="AI Agent Console")
        self._connections: set[WebSocket] = set()
        self._slam_connections: set[WebSocket] = set()
        self._server = None
        self._command_callback: Optional[Callable] = None

        # 当前状态快照 (新连接时发送)
        self._state: dict[str, Any] = {
            "status": "未连接",
            "connected": False,
            "text": "待命",
            "emotion": "",
            "button_text": "开始对话",
            "auto_mode": False,
        }

        self._setup_routes()

    def set_command_callback(self, callback: Callable) -> None:
        """设置控制指令回调."""
        self._command_callback = callback

    def update_state(self, key: str, value: Any) -> None:
        """更新状态快照中的某个字段."""
        self._state[key] = value

    # ===================== 路由 =====================

    def _setup_routes(self):
        app = self.app

        # 首页
        @app.get("/")
        async def index():
            index_file = STATIC_DIR / "index.html"
            return HTMLResponse(index_file.read_text(encoding="utf-8"))

        # SLAM 可视化页面
        @app.get("/slam")
        async def slam_page():
            slam_file = STATIC_DIR / "slam.html"
            return HTMLResponse(slam_file.read_text(encoding="utf-8"))

        # 静态文件
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        # 表情资源
        @app.get("/emojis/{filename}")
        async def emoji_file(filename: str):
            assets_dir = find_assets_dir()
            if not assets_dir:
                return HTMLResponse("Not found", status_code=404)
            file_path = assets_dir / "emojis" / filename
            if not file_path.exists():
                return HTMLResponse("Not found", status_code=404)
            return FileResponse(str(file_path))

        # WebSocket — 控制信令
        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket):
            await self._handle_ws(websocket)

        # WebSocket — SLAM 二进制流 (单独通道避免与控制信令互相阻塞)
        @app.websocket("/ws/slam")
        async def ws_slam_endpoint(websocket: WebSocket):
            await self._handle_slam_ws(websocket)

    # ===================== WebSocket 处理 =====================

    async def _handle_ws(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.info(
            "WebSocket 客户端已连接, 当前连接数: %d", len(self._connections)
        )

        try:
            # 发送完整状态快照
            await websocket.send_json({"type": "snapshot", **self._state})

            # 接收控制指令
            while True:
                data = await websocket.receive_json()
                action = data.get("action")
                if action and self._command_callback:
                    try:
                        await self._command_callback(action, data)
                    except Exception as e:
                        logger.error("处理控制指令失败: %s", e)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("WebSocket 连接异常: %s", e)
        finally:
            self._connections.discard(websocket)
            logger.info(
                "WebSocket 客户端已断开, 当前连接数: %d", len(self._connections)
            )

    async def _handle_slam_ws(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._slam_connections.add(websocket)
        logger.info("SLAM WS 已连接, 当前连接数: %d", len(self._slam_connections))
        try:
            while True:
                # 当前 SLAM 通道是单向推流, 仅保活
                await websocket.receive_bytes()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("SLAM WS 连接异常: %s", e)
        finally:
            self._slam_connections.discard(websocket)
            logger.info("SLAM WS 已断开, 当前连接数: %d", len(self._slam_connections))

    async def broadcast_slam_bytes(self, payload: bytes) -> None:
        """广播二进制 SLAM 帧给所有 /ws/slam 连接."""
        if not self._slam_connections:
            return
        dead: list[WebSocket] = []
        for ws in self._slam_connections:
            try:
                await ws.send_bytes(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._slam_connections.discard(ws)

    async def broadcast(self, data: dict) -> None:
        """广播消息给所有活跃的 WebSocket 连接."""
        if not self._connections:
            return

        message = json.dumps(data, ensure_ascii=False)
        dead: list[WebSocket] = []

        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections.discard(ws)

    # ===================== 服务器生命周期 =====================

    async def start(self) -> None:
        """非阻塞方式启动 uvicorn 服务器."""
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        logger.info("Web 服务器启动: http://%s:%d", self.host, self.port)
        await self._server.serve()

    async def stop(self) -> None:
        """停止服务器."""
        if self._server:
            self._server.should_exit = True
            logger.info("Web 服务器已停止")

        # 关闭所有连接
        for ws in list(self._connections) + list(self._slam_connections):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._slam_connections.clear()
