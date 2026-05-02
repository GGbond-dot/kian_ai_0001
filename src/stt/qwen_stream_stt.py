"""
Qwen 流式 STT 模块（paraformer-realtime-v2）

通过 dashscope WebSocket duplex 协议边录边传，松开按钮时立即拿最终文本，
把 STT 等待时间压到几十~几百毫秒。

接口：
  await stt.start_session()      # listen.start 时调
  await stt.feed(pcm_bytes)      # feed_external_pcm 时同步推帧
  text = await stt.finish(timeout=1.5)  # listen.stop 时收尾，超时返回 None 触发降级
  await stt.abort()              # 强制断开（取消 / 异常）

协议参考：dashscope paraformer-realtime WebSocket duplex
  wss://dashscope.aliyuncs.com/api-ws/v1/inference
"""
import asyncio
import json
import os
import uuid
from typing import Optional

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


class QwenStreamSTT:
    def __init__(self):
        config = ConfigManager.get_instance()
        self._language = config.get_config("STT.language", "zh")
        self._model = config.get_config(
            "STT.qwen_stream_model", "paraformer-realtime-v2"
        )
        self._api_key = (
            config.get_config("STT.dashscope_api_key", "")
            or config.get_config("CAMERA.VLapi_key", "")
            or os.getenv("DASHSCOPE_API_KEY", "")
        )
        self._ws = None
        self._task_id: Optional[str] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._started_evt: Optional[asyncio.Event] = None
        self._finished_evt: Optional[asyncio.Event] = None
        self._final_text: str = ""
        self._partial_text: str = ""
        self._failed = False
        self._closed = True

    async def start_session(self) -> bool:
        """建立 WebSocket 并发 run-task。返回 True 表示握手成功，False 触发降级。"""
        if not self._api_key:
            logger.warning("[stream-stt] 未配置 dashscope api key，跳过流式")
            return False
        try:
            import websockets
        except ImportError:
            logger.warning("[stream-stt] websockets 未安装")
            return False

        self._task_id = uuid.uuid4().hex
        self._final_text = ""
        self._partial_text = ""
        self._failed = False
        self._started_evt = asyncio.Event()
        self._finished_evt = asyncio.Event()
        try:
            self._ws = await websockets.connect(
                WS_URL,
                additional_headers={
                    "Authorization": f"bearer {self._api_key}",
                    "X-DashScope-DataInspection": "enable",
                },
                max_size=2 ** 23,
                ping_interval=None,
            )
            self._closed = False
        except TypeError:
            # websockets<10 用 extra_headers
            self._ws = await websockets.connect(
                WS_URL,
                extra_headers={
                    "Authorization": f"bearer {self._api_key}",
                    "X-DashScope-DataInspection": "enable",
                },
                max_size=2 ** 23,
                ping_interval=None,
            )
            self._closed = False
        except Exception as e:
            logger.warning("[stream-stt] WS 连接失败：%s", e)
            return False

        run_task = {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "language_hints": [self._language] if self._language else ["zh"],
                },
                "input": {},
            },
        }
        try:
            await self._ws.send(json.dumps(run_task))
        except Exception as e:
            logger.warning("[stream-stt] run-task 发送失败：%s", e)
            await self._safe_close()
            return False

        self._recv_task = asyncio.create_task(
            self._recv_loop(), name="qwen-stream-stt-recv"
        )

        try:
            await asyncio.wait_for(self._started_evt.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning("[stream-stt] task-started 等待超时")
            await self._safe_close()
            return False

        if self._failed:
            await self._safe_close()
            return False
        logger.info("[stream-stt] 会话就绪 task_id=%s model=%s", self._task_id, self._model)
        return True

    async def _recv_loop(self):
        try:
            async for msg in self._ws:
                if isinstance(msg, bytes):
                    continue
                try:
                    obj = json.loads(msg)
                except Exception:
                    continue
                header = obj.get("header") or {}
                event = header.get("event")
                if event == "task-started":
                    self._started_evt.set()
                elif event == "result-generated":
                    sentence = (
                        obj.get("payload", {}).get("output", {}).get("sentence") or {}
                    )
                    text = sentence.get("text") or ""
                    if sentence.get("sentence_end"):
                        # 流式 STT 可能切多句；末段拼接即可
                        if self._final_text:
                            self._final_text += text
                        else:
                            self._final_text = text
                        self._partial_text = ""
                    else:
                        self._partial_text = text
                elif event == "task-finished":
                    if self._partial_text and not self._final_text:
                        self._final_text = self._partial_text
                    self._finished_evt.set()
                    break
                elif event == "task-failed":
                    err = header.get("error_message") or header.get("error_code") or "unknown"
                    logger.warning("[stream-stt] task-failed：%s", err)
                    self._failed = True
                    self._started_evt.set()
                    self._finished_evt.set()
                    break
        except Exception as e:
            logger.warning("[stream-stt] recv 异常：%s", e)
            self._failed = True
            if self._started_evt and not self._started_evt.is_set():
                self._started_evt.set()
            if self._finished_evt and not self._finished_evt.is_set():
                self._finished_evt.set()

    async def feed(self, pcm_bytes: bytes) -> None:
        if self._closed or self._failed or not self._ws or not pcm_bytes:
            return
        try:
            await self._ws.send(pcm_bytes)
        except Exception as e:
            logger.debug("[stream-stt] feed 异常：%s", e)
            self._failed = True

    async def finish(self, timeout: float = 1.5) -> Optional[str]:
        """发 finish-task 并等 task-finished。失败/超时返回 None 触发降级。"""
        if self._closed or self._ws is None:
            return None
        if self._failed:
            await self._safe_close()
            return None
        finish_msg = {
            "header": {
                "action": "finish-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }
        try:
            await self._ws.send(json.dumps(finish_msg))
        except Exception as e:
            logger.warning("[stream-stt] finish-task 发送失败：%s", e)
            await self._safe_close()
            return None

        try:
            await asyncio.wait_for(self._finished_evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[stream-stt] task-finished 等待超时 %.1fs", timeout)
            await self._safe_close()
            return None

        await self._safe_close()
        if self._failed:
            return None
        text = (self._final_text or "").strip()
        logger.info("[stream-stt] 最终结果：'%s'", text)
        return text

    async def abort(self) -> None:
        await self._safe_close()

    async def _safe_close(self):
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._recv_task and not self._recv_task.done():
            try:
                await asyncio.wait_for(self._recv_task, timeout=0.5)
            except Exception:
                self._recv_task.cancel()
        self._recv_task = None

    def preload(self) -> None:
        return None
