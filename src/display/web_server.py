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
import os
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.ros.grasp_task_bridge import (
    GRASP_DEFAULT_INTERRUPT_MODE,
    GRASP_GOAL_TYPE_PICKUP,
    GRASP_GOAL_Z,
)
from src.utils.logging_config import get_logger
from src.utils.resource_finder import find_assets_dir
from src.utils.config_manager import ConfigManager

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
        self._video_connections: set[WebSocket] = set()
        self._audio_in_connections: set[WebSocket] = set()
        self._audio_out_connections: set[WebSocket] = set()
        self._server = None
        self._command_callback: Optional[Callable] = None
        self._audio_in_callback: Optional[Callable] = None
        self._nofly_zone_callback: Optional[Callable] = None
        # 平板回报 tts_failed 等上行 JSON 的回调
        # 签名: async (msg: dict) -> None
        self._audio_out_text_callback: Optional[Callable] = None

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
        self._nofly_zones: list[dict[str, Any]] = []
        self._nofly_updated_at = 0.0

        # 抓取任务 (单目标,框选中心 cx/cy + 常量 z)
        self._grasp_task: Optional[dict[str, Any]] = None
        self._grasp_updated_at = 0.0
        self._grasp_task_callback: Optional[Callable] = None

        self._setup_routes()

    def set_command_callback(self, callback: Callable) -> None:
        """设置控制指令回调."""
        self._command_callback = callback

    def set_audio_out_text_callback(self, callback: Callable) -> None:
        """设置平板侧上行 JSON 回调（如 tts_failed）。

        callback 签名: async (msg: dict) -> None
        """
        self._audio_out_text_callback = callback

    def set_audio_in_callback(self, callback: Callable) -> None:
        """设置平板麦克风 PCM 入流回调.

        callback 签名: async (pcm_bytes: bytes, meta: dict) -> None
        meta 含: captured_at_ms (平板侧时间戳), recv_at_ms (后端收到时刻)
        """
        self._audio_in_callback = callback

    def set_nofly_zone_callback(self, callback: Callable) -> None:
        """设置禁飞区下发回调.

        callback 签名: async (payload: dict) -> dict | None
        """
        self._nofly_zone_callback = callback

    def set_grasp_task_callback(self, callback: Callable) -> None:
        """设置抓取任务下发回调.

        callback 签名: async (payload: dict) -> dict | None
        payload 含 cx/cy/z/goal_type/interrupt_mode/frame_id/source/updated_at
        """
        self._grasp_task_callback = callback

    def update_state(self, key: str, value: Any) -> None:
        """更新状态快照中的某个字段."""
        self._state[key] = value

    @staticmethod
    def _global_map_path() -> Path:
        configured = ConfigManager.get_instance().get_config(
            "GLOBAL_PLANNER.pcd_path", "maps/global_map_ds.pcd"
        )
        path = Path(str(configured))
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parents[2] / path

    
    def _validate_nofly_zones(self, zones: Any) -> list[dict[str, Any]]:
        if not isinstance(zones, list):
            raise ValueError("zones_must_be_list")

        normalized: list[dict[str, Any]] = []
        for index, zone in enumerate(zones):
            if not isinstance(zone, dict):
                raise ValueError(f"zone_{index}_must_be_object")
            try:
                min_x = float(zone["minX"])
                max_x = float(zone["maxX"])
                min_y = float(zone["minY"])
                max_y = float(zone["maxY"])
                z_min = float(zone["zMin"])
                z_max = float(zone["zMax"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"zone_{index}_invalid_numeric_fields") from exc

            if min_x > max_x:
                min_x, max_x = max_x, min_x
            if min_y > max_y:
                min_y, max_y = max_y, min_y
            if z_min > z_max:
                z_min, z_max = z_max, z_min
            if max_x - min_x <= 0 or max_y - min_y <= 0:
                raise ValueError(f"zone_{index}_empty_rect")

            normalized.append({
                "id": str(zone.get("id") or f"zone-{index + 1}"),
                "name": str(zone.get("name") or f"禁飞区 {index + 1}"),
                "minX": min_x,
                "maxX": max_x,
                "minY": min_y,
                "maxY": max_y,
                "zMin": z_min,
                "zMax": z_max,
            })
        return normalized

    def _validate_grasp_task(self, payload: Any) -> dict[str, Any]:
        """校验前端框选矩形并塌缩成中心点 (cx, cy)。

        前端传矩形 (与禁飞区前后端职责一致),后端算中心。z 用常量,
        规划器忽略 (见设计文档 §三)。
        """
        if not isinstance(payload, dict):
            raise ValueError("payload_must_be_object")
        try:
            min_x = float(payload["minX"])
            max_x = float(payload["maxX"])
            min_y = float(payload["minY"])
            max_y = float(payload["maxY"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_numeric_fields") from exc

        if min_x > max_x:
            min_x, max_x = max_x, min_x
        if min_y > max_y:
            min_y, max_y = max_y, min_y
        if max_x - min_x <= 0 or max_y - min_y <= 0:
            raise ValueError("empty_rect")

        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2

        return {
            "cx": cx,
            "cy": cy,
            "z": GRASP_GOAL_Z,
            "goal_type": GRASP_GOAL_TYPE_PICKUP,
            "interrupt_mode": 0,
            "dwell_time": 0.0,
            "yaw_deg": -1.0,
            "frame_id": str(payload.get("frame_id") or "world"),
            "source": str(payload.get("source") or "slam_web"),
            "rect": {
                "minX": min_x,
                "maxX": max_x,
                "minY": min_y,
                "maxY": max_y,
            },
            "updated_at": time.time(),
        }

    # ===================== 路由 =====================

    def _setup_routes(self):
        app = self.app

        # 首页
        @app.get("/")
        async def index():
            index_file = STATIC_DIR / "index.html"
            # 禁止缓存 HTML 本体；脚本通过 ?v=N 自带缓存破除
            return HTMLResponse(
                index_file.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        # SLAM 可视化页面
        @app.get("/slam")
        async def slam_page():
            slam_file = STATIC_DIR / "slam.html"
            return HTMLResponse(
                slam_file.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        # 平板直连 TTS PoC（临时验证页，方案敲定后删）
        @app.get("/tts_poc")
        async def tts_poc_page():
            poc_file = STATIC_DIR / "tts_poc.html"
            return HTMLResponse(poc_file.read_text(encoding="utf-8"))

        @app.get("/api/tablet_tts_config")
        async def tablet_tts_config():
            return {
                "api_key": os.getenv("DASHSCOPE_TABLET_TTS_API_KEY", ""),
                "url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                "model": "qwen3-tts-flash",
                "voice": "Cherry",
            }

        @app.get("/api/global_map.pcd")
        async def global_map():
            """Return the same local offline map used by the Kian planner."""
            return FileResponse(
                str(self._global_map_path()),
                media_type="application/octet-stream",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/api/noflyzone")
        async def get_noflyzone():
            return {
                "ok": True,
                "zones": self._nofly_zones,
                "count": len(self._nofly_zones),
                "updated_at": self._nofly_updated_at,
                "ros_publish_configured": self._nofly_zone_callback is not None,
            }

        @app.post("/api/noflyzone")
        async def post_noflyzone(request: Request):
            try:
                payload = await request.json()
                zones = self._validate_nofly_zones(payload.get("zones", []))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                logger.warning("禁飞区 payload 解析失败: %s", exc)
                return {"ok": False, "error": "invalid_json"}

            normalized = {
                "zones": zones,
                "count": len(zones),
                "frame_id": payload.get("frame_id") or "a/camera_init",
                "source": payload.get("source") or "slam_web",
                "updated_at": time.time(),
            }
            self._nofly_zones = zones
            self._nofly_updated_at = normalized["updated_at"]

            publish_result = {"configured": False, "published": False}
            if self._nofly_zone_callback is not None:
                try:
                    result = self._nofly_zone_callback(normalized)
                    if asyncio.iscoroutine(result):
                        result = await result
                    publish_result = result or {"configured": True, "published": False}
                except Exception as exc:
                    logger.error("禁飞区下发回调失败: %s", exc, exc_info=True)
                    return {"ok": False, "error": "publish_failed", "detail": str(exc)}

            logger.info("禁飞区已接收: count=%d frame_id=%s publish=%s", len(zones), normalized["frame_id"], publish_result)
            return {
                "ok": True,
                "count": len(zones),
                "updated_at": self._nofly_updated_at,
                "publish": publish_result,
            }

        @app.get("/api/grasp_task")
        async def get_grasp_task():
            return {
                "ok": True,
                "task": self._grasp_task,
                "updated_at": self._grasp_updated_at,
                "ros_publish_configured": self._grasp_task_callback is not None,
            }

        @app.post("/api/grasp_task")
        async def post_grasp_task(request: Request):
            try:
                payload = await request.json()
                normalized = self._validate_grasp_task(payload)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                logger.warning("抓取任务 payload 解析失败: %s", exc)
                return {"ok": False, "error": "invalid_json"}

            self._grasp_task = normalized
            self._grasp_updated_at = normalized["updated_at"]

            publish_result = {"configured": False, "published": False}
            if self._grasp_task_callback is not None:
                try:
                    result = self._grasp_task_callback(normalized)
                    if asyncio.iscoroutine(result):
                        result = await result
                    publish_result = result or {"configured": True, "published": False}
                except Exception as exc:
                    logger.error("抓取任务下发回调失败: %s", exc, exc_info=True)
                    return {"ok": False, "error": "publish_failed", "detail": str(exc)}

            logger.info(
                "抓取任务已接收: cx=%.3f cy=%.3f goal_type=%d interrupt=%d publish=%s",
                normalized["cx"], normalized["cy"],
                normalized["goal_type"], normalized["interrupt_mode"],
                publish_result,
            )
            return {
                "ok": True,
                "task": normalized,
                "updated_at": self._grasp_updated_at,
                "publish": publish_result,
            }

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

        # WebSocket — 视频标注帧推送 (JPEG 二进制)
        @app.websocket("/ws/video")
        async def ws_video_endpoint(websocket: WebSocket):
            await self._handle_video_ws(websocket)

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

    async def _handle_video_ws(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._video_connections.add(websocket)
        logger.info("Video WS 已连接, 当前连接数: %d", len(self._video_connections))
        try:
            while True:
                await websocket.receive_bytes()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Video WS 连接异常: %s", e)
        finally:
            self._video_connections.discard(websocket)
            logger.info("Video WS 已断开, 当前连接数: %d", len(self._video_connections))

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
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                # 文本帧：平板上行 JSON（tts_failed 等）
                text = msg.get("text")
                if text is not None and self._audio_out_text_callback is not None:
                    try:
                        data = json.loads(text)
                    except Exception:
                        logger.debug("audio_out 文本帧 JSON 解析失败: %r", text[:200])
                        continue
                    try:
                        await self._audio_out_text_callback(data)
                    except Exception as e:
                        logger.error("audio_out 文本回调失败: %s", e)
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

    async def broadcast_audio_out_text(self, msg: dict) -> int:
        """把 JSON 文本帧广播给所有 /ws/audio_out 连接（用于 tts_text 等下行控制）。"""
        if not self._audio_out_connections:
            return 0
        payload = json.dumps(msg, ensure_ascii=False)
        dead: list[WebSocket] = []
        ok = 0
        for ws in self._audio_out_connections:
            try:
                await ws.send_text(payload)
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

    async def broadcast_video_frame(self, jpeg_bytes: bytes) -> None:
        """广播 JPEG 视频帧到所有 /ws/video 连接."""
        if not self._video_connections:
            return
        dead: list[WebSocket] = []
        for ws in self._video_connections:
            try:
                await ws.send_bytes(jpeg_bytes)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._video_connections.discard(ws)

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
            + list(self._video_connections)
            + list(self._audio_in_connections)
            + list(self._audio_out_connections)
        ):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._slam_connections.clear()
        self._video_connections.clear()
        self._audio_in_connections.clear()
        self._audio_out_connections.clear()
