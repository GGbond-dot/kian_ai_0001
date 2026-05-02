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
import time
import wave
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
        self._audio_in_connections: set[WebSocket] = set()
        self._audio_out_connections: set[WebSocket] = set()
        self._server = None
        self._command_callback: Optional[Callable] = None
        self._audio_in_callback: Optional[Callable] = None

        # 平板麦克风调试落盘目录（每次连接新建一个 wav）
        self._audio_in_dump_dir = Path("logs/tablet_audio_in")
        self._audio_in_sample_rate = 16000

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

    def set_audio_in_callback(self, callback: Callable) -> None:
        """设置平板麦克风 PCM 入流回调.

        callback 签名: async (pcm_bytes: bytes, meta: dict) -> None
        meta 含: captured_at_ms (平板侧时间戳), recv_at_ms (后端收到时刻)
        """
        self._audio_in_callback = callback

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

        # 平板直连 TTS PoC（临时验证页，方案敲定后删）
        @app.get("/tts_poc")
        async def tts_poc_page():
            poc_file = STATIC_DIR / "tts_poc.html"
            return HTMLResponse(poc_file.read_text(encoding="utf-8"))

        # 静态文件
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        # PWA: manifest 和 service worker 必须挂在根路径
        # SW 的 scope 受文件 URL 限制，挂 /sw.js 才能控制整站；manifest 同理放根更稳
        @app.get("/manifest.json")
        async def manifest():
            return FileResponse(
                str(STATIC_DIR / "manifest.json"),
                media_type="application/manifest+json",
            )

        @app.get("/sw.js")
        async def service_worker():
            response = FileResponse(
                str(STATIC_DIR / "sw.js"),
                media_type="application/javascript",
            )
            # SW 文件本身不能被缓存，否则更新不及时
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

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

        # WebSocket — 平板麦克风 PCM 入流 (16kHz / 16bit / mono)
        # 协议: 客户端先发一帧 JSON 元数据 {sample_rate, frame_ms, ...}
        # 之后每帧二进制 = 8 字节小端 captured_at_ms + 原始 PCM
        @app.websocket("/ws/audio_in")
        async def ws_audio_in_endpoint(websocket: WebSocket):
            await self._handle_audio_in_ws(websocket)

        # WebSocket — 服务端推 TTS 音频到平板播放
        # 后端把整段 mp3 二进制发给所有连上的客户端
        @app.websocket("/ws/audio_out")
        async def ws_audio_out_endpoint(websocket: WebSocket):
            await self._handle_audio_out_ws(websocket)

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

    async def _handle_audio_in_ws(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._audio_in_connections.add(websocket)
        logger.info(
            "平板麦克风 WS 已连接, 当前连接数: %d", len(self._audio_in_connections)
        )

        # 调试落盘
        self._audio_in_dump_dir.mkdir(parents=True, exist_ok=True)
        wav_path = self._audio_in_dump_dir / f"audio_in_{int(time.time())}.wav"
        wav_writer: Optional[wave.Wave_write] = None
        total_bytes = 0
        first_chunk_log_done = False

        try:
            wav_writer = wave.open(str(wav_path), "wb")
            wav_writer.setnchannels(1)
            wav_writer.setsampwidth(2)
            wav_writer.setframerate(self._audio_in_sample_rate)

            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                # 文本帧: 元数据 / 控制
                if msg.get("text") is not None:
                    try:
                        meta = json.loads(msg["text"])
                    except Exception:
                        continue
                    if meta.get("sample_rate"):
                        self._audio_in_sample_rate = int(meta["sample_rate"])
                        wav_writer.setframerate(self._audio_in_sample_rate)
                    logger.info("平板麦克风 元数据: %s", meta)
                    continue
                # 二进制帧: 8 字节 captured_at_ms + PCM
                payload = msg.get("bytes")
                if not payload or len(payload) < 8:
                    continue
                recv_at_ms = int(time.time() * 1000)
                captured_at_ms = int.from_bytes(payload[:8], "little", signed=False)
                pcm = payload[8:]

                wav_writer.writeframes(pcm)
                total_bytes += len(pcm)

                if not first_chunk_log_done:
                    logger.info(
                        "平板麦克风 首帧到达: bytes=%d, 端到端延时=%dms",
                        len(pcm),
                        recv_at_ms - captured_at_ms,
                    )
                    first_chunk_log_done = True

                if self._audio_in_callback:
                    try:
                        await self._audio_in_callback(
                            pcm,
                            {"captured_at_ms": captured_at_ms, "recv_at_ms": recv_at_ms},
                        )
                    except Exception as e:
                        logger.error("音频回调失败: %s", e)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("平板麦克风 WS 异常: %s", e)
        finally:
            if wav_writer is not None:
                try:
                    wav_writer.close()
                except Exception:
                    pass
            self._audio_in_connections.discard(websocket)
            logger.info(
                "平板麦克风 WS 已断开, 累计 %d 字节, 落盘: %s",
                total_bytes,
                wav_path,
            )

    async def _handle_audio_out_ws(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._audio_out_connections.add(websocket)
        logger.info(
            "TTS 输出 WS 已连接, 当前连接数: %d", len(self._audio_out_connections)
        )
        try:
            while True:
                # 当前是单向推流，仅保活；如果客户端发文本帧，忽略
                await websocket.receive()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("TTS 输出 WS 异常: %s", e)
        finally:
            self._audio_out_connections.discard(websocket)
            logger.info(
                "TTS 输出 WS 已断开, 当前连接数: %d", len(self._audio_out_connections)
            )

    def has_audio_out_listeners(self) -> bool:
        return len(self._audio_out_connections) > 0

    async def broadcast_audio_out(self, mp3_bytes: bytes) -> int:
        """把 mp3 字节广播给所有 /ws/audio_out 连接，返回成功推送的客户端数。"""
        if not self._audio_out_connections:
            return 0
        dead: list[WebSocket] = []
        ok = 0
        for ws in self._audio_out_connections:
            try:
                await ws.send_bytes(mp3_bytes)
                ok += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._audio_out_connections.discard(ws)
        return ok

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
        for ws in (
            list(self._connections)
            + list(self._slam_connections)
            + list(self._audio_in_connections)
            + list(self._audio_out_connections)
        ):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._slam_connections.clear()
        self._audio_in_connections.clear()
        self._audio_out_connections.clear()
