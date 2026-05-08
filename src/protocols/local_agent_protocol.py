"""
LocalAgentProtocol - 完全本地化的 AI Agent 协议实现

替代 WebsocketProtocol / MqttProtocol，驱动本地 LLM + STT + TTS
完成整个对话循环，无需连接任何外部 AI 服务器。

流程：
    麦克风 Opus 帧 → STT Provider → 文字 →
    LLMAgent (OpenAI function calling + McpTools) → 回复文字 →
    TTS Provider → Opus 帧 → AudioCodec 播放
"""
import asyncio
import json
import re
import time
from typing import Callable, List, Optional

import numpy as np

from src.constants.constants import AudioConfig
from src.protocols.protocol import Protocol
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ── 在 AudioCodec 初始化之前强制覆盖本地语音链路参数 ──────────────────────
# 本地 TTS 输出的 Opus 编码采用 16kHz / 20ms，此处确保 AudioCodec 编解码侧一致。
# 该赋值在 LocalAgentProtocol 被导入时立即生效（协议在插件初始化前创建）。
AudioConfig.OUTPUT_SAMPLE_RATE = 16000
AudioConfig.FRAME_DURATION = 20
AudioConfig.INPUT_FRAME_SIZE = int(
    AudioConfig.INPUT_SAMPLE_RATE * (AudioConfig.FRAME_DURATION / 1000)
)
AudioConfig.OUTPUT_FRAME_SIZE = int(
    AudioConfig.OUTPUT_SAMPLE_RATE * (AudioConfig.FRAME_DURATION / 1000)
)

_SMALL_TALK_INPUTS = {
    "hi",
    "hello",
    "hey",
    "nihao",
    "niaho",
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "在吗",
    "在嘛",
    "早上好",
    "中午好",
    "下午好",
    "晚上好",
}


class LocalAgentProtocol(Protocol):
    """
    本地 Agent 协议：完全自主运行，不连接任何远端 AI 服务。

    外部接口与 WebsocketProtocol 完全兼容，Application 无需修改。
    """

    def __init__(self):
        super().__init__()
        self.session_id = "local-agent-session"
        self.config = ConfigManager.get_instance()

        # 初始化状态
        self._audio_channel_open: bool = False
        self._is_listening: bool = False
        self._opus_buffer: List[bytes] = []
        self._listen_mode: str = "manual"

        # 外部 PCM 输入通道（例如平板 WebView 推过来的 16kHz/16bit/单声道 PCM）
        # 与 _opus_buffer 互斥使用：本轮谁有数据走谁
        self._external_pcm_chunks: List[bytes] = []

        # 流式 STT 会话（paraformer-realtime-v2）：边录边传，停止时立刻拿最终文本
        # 失败/超时则自动降级到批量 transcribe_pcm（缓冲始终保留作兜底）
        self._stream_stt = None
        self._stream_stt_active: bool = False

        # TTS 远端输出（平板播放）
        # _tts_remote_sink: async (mp3_bytes) -> int  返回成功推送的客户端数
        # _tts_remote_has_listeners: () -> bool       是否有平板正连着
        self._tts_remote_sink: Optional[Callable] = None
        self._tts_remote_has_listeners: Optional[Callable] = None
        # 平板直连 TTS：JSON 下行（async (msg: dict) -> int）
        self._tts_remote_text_sink: Optional[Callable] = None

        # 平板直连段管理
        self._tablet_segment_id: int = 0
        self._tablet_segment_text: dict[int, str] = {}     # 已下发但未确认成功的段
        self._tablet_failed_handled: set[int] = set()       # 已进过 fallback 的段（不重试两次）
        self._tablet_consecutive_fail: int = 0              # 连续失败计数
        self._tablet_session_disabled: bool = False         # 本轮对话临时禁用直连

        # 正在进行的 pipeline 任务（用于取消）
        self._pipeline_task: Optional[asyncio.Task] = None
        self._auto_stop_task: Optional[asyncio.Task] = None
        self._stop_lock = asyncio.Lock()

        # edge-tts 预热：和 LLM 推理并行做 TLS+WSS 握手
        self._tts_warmup_task: Optional[asyncio.Task] = None
        # qwen-flash 预热：在录音开始时主动握手，等 STT 出来时连接已热
        self._llm_fast_warmup_task: Optional[asyncio.Task] = None
        self._llm_fast_last_warmup_ms: float = 0.0

        # 懒加载的模块实例
        self._stt = None
        self._tts = None
        self._agent = None
        self._vad = None
        self._vad_decoder = None
        self._vad_backend = "disabled"

        # 本地自动断句状态
        self._speech_started = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._frame_duration_ms = 20
        self._vad_aggressiveness = int(
            self.config.get_config("LOCAL_AGENT.VAD_AGGRESSIVENESS", 2)
        )
        silence_ms = int(
            self.config.get_config("STT.vad_min_silence_duration_ms", 500)
        )
        min_voice_ms = int(
            self.config.get_config("LOCAL_AGENT.AUTO_STOP_MIN_VOICE_MS", 240)
        )
        self._max_listen_seconds = float(
            self.config.get_config("LOCAL_AGENT.AUTO_STOP_MAX_LISTEN_SECONDS", 15.0)
        )
        self._energy_threshold = int(
            self.config.get_config("LOCAL_AGENT.ENERGY_THRESHOLD", 180)
        )
        self._silence_limit_frames = max(1, silence_ms // self._frame_duration_ms)
        self._min_speech_frames = max(1, min_voice_ms // self._frame_duration_ms)

        logger.info("LocalAgentProtocol 已创建（本地模式，无外部 AI 服务依赖）")

    # ─────────────────────────────────────────────────────────────────
    # 懒加载辅助
    # ─────────────────────────────────────────────────────────────────
    def _get_stt(self):
        if self._stt is None:
            provider = self._get_stt_provider_name()
            if provider in {"qwen", "qwen_asr", "qwen-asr", "dashscope"}:
                from src.stt.qwen_asr_stt import QwenASRSTT

                self._stt = QwenASRSTT()
            else:
                from src.stt.whisper_stt import WhisperSTT

                self._stt = WhisperSTT()
        return self._stt

    def _get_tts(self):
        if self._tts is None:
            provider = self._get_tts_provider_name()
            if provider in {"qwen", "qwen_tts", "qwen-tts", "dashscope"}:
                from src.tts.qwen_tts_client import QwenTTSClient

                self._tts = QwenTTSClient()
            else:
                from src.tts.edge_tts_client import EdgeTTSClient

                self._tts = EdgeTTSClient()
        return self._tts

    def _get_stt_provider_name(self) -> str:
        return str(self.config.get_config("STT.provider", "whisper")).strip().lower()

    async def _kick_stream_stt_start(self) -> None:
        try:
            stt = self._get_stream_stt()
            ok = await stt.start_session()
            if not ok or not self._is_listening:
                self._stream_stt_active = False
                return
            # 重放握手期间积压的 chunks，反复直到稳定（buffer 长度不再增长）。
            # 最后一次 break → active=True 之间无 await，feed_external_pcm 无法插入丢帧。
            fed = 0
            while True:
                end = len(self._external_pcm_chunks)
                if end == fed:
                    break
                for i in range(fed, end):
                    await stt.feed(self._external_pcm_chunks[i])
                fed = end
            self._stream_stt_active = True
        except Exception as e:
            logger.warning("[stream-stt] 启动会话失败：%s", e)
            self._stream_stt_active = False

    def _is_stream_stt_enabled(self) -> bool:
        if not bool(self.config.get_config("STT.streaming_enabled", False)):
            return False
        provider = self._get_stt_provider_name()
        return provider in {"qwen", "qwen_asr", "qwen-asr", "dashscope"}

    def _get_stream_stt(self):
        if self._stream_stt is None:
            from src.stt.qwen_stream_stt import QwenStreamSTT

            self._stream_stt = QwenStreamSTT()
        return self._stream_stt

    def _get_tts_provider_name(self) -> str:
        return str(self.config.get_config("TTS.provider", "edge")).strip().lower()

    def _get_agent(self):
        if self._agent is None:
            from src.llm.agent import LLMAgent
            self._agent = LLMAgent()
        return self._agent

    def _get_mcp_server(self):
        from src.mcp.mcp_server import McpServer
        return McpServer.get_instance()

    def _get_vad(self):
        if self._vad is not None:
            return self._vad
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(self._vad_aggressiveness)
            self._vad_backend = "webrtcvad"
            logger.info(
                "LocalAgentProtocol：自动断句启用 webrtcvad，aggressiveness=%s",
                self._vad_aggressiveness,
            )
        except Exception as e:
            self._vad = False
            self._vad_backend = "energy"
            logger.warning(
                "LocalAgentProtocol：webrtcvad 不可用，回退到能量阈值断句: %s",
                e,
            )
        return self._vad

    def _get_vad_decoder(self):
        if self._vad_decoder is not None:
            return self._vad_decoder
        import opuslib

        self._vad_decoder = opuslib.Decoder(AudioConfig.INPUT_SAMPLE_RATE, 1)
        return self._vad_decoder

    def _reset_listen_tracking(self):
        self._speech_started = False
        self._speech_frames = 0
        self._silence_frames = 0

    def _should_skip_tools_for_text(self, user_text: str) -> bool:
        """
        对纯问候/寒暄关闭工具，避免模型在简单打招呼时误触发 MCP 工具。
        """
        normalized = re.sub(r"[\s\W_]+", "", (user_text or "").strip().lower())
        return normalized in _SMALL_TALK_INPUTS

    def _extract_tool_text(self, tool_result: str) -> str:
        try:
            data = json.loads(tool_result)
        except Exception:
            return str(tool_result)
        content = data.get("content") or []
        if isinstance(content, list):
            texts = [
                str(item.get("text", "")).strip()
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            merged = "\n".join(text for text in texts if text).strip()
            if merged:
                return merged
        return str(tool_result)

    def _should_auto_stop(self) -> bool:
        return self._listen_mode in ("auto", "realtime")

    def _is_speech_frame(self, pcm_data: bytes) -> bool:
        vad = self._get_vad()
        if vad not in (None, False):
            try:
                return bool(vad.is_speech(pcm_data, AudioConfig.INPUT_SAMPLE_RATE))
            except Exception as e:
                logger.debug("LocalAgentProtocol：webrtcvad 检测失败，回退能量阈值: %s", e)
        try:
            samples = np.frombuffer(pcm_data, dtype=np.int16)
            if samples.size == 0:
                return False
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))))
            return rms >= self._energy_threshold
        except Exception:
            return False

    async def _finish_listening(self, trigger: str):
        async with self._stop_lock:
            if not self._is_listening:
                return

            logger.info("LocalAgentProtocol：结束录音，原因=%s", trigger)
            self._is_listening = False
            frames = list(self._opus_buffer)
            pcm_chunks = list(self._external_pcm_chunks)
            self._opus_buffer.clear()
            self._external_pcm_chunks.clear()
            self._reset_listen_tracking()

            current_task = asyncio.current_task()
            if (
                self._auto_stop_task
                and self._auto_stop_task is not current_task
                and not self._auto_stop_task.done()
            ):
                self._auto_stop_task.cancel()
                try:
                    await self._auto_stop_task
                except asyncio.CancelledError:
                    pass
            self._auto_stop_task = None

            if self._pipeline_task and not self._pipeline_task.done():
                self._pipeline_task.cancel()

            # 流式 STT 会话取下来交给后续逻辑：pcm 分支用它 finish，其他分支 abort
            stream_stt = self._stream_stt if self._stream_stt_active else None
            self._stream_stt_active = False
            self._stream_stt = None

            # 优先用外部 PCM（平板等远端麦克风），其次本地 Opus 帧
            if pcm_chunks:
                pcm_total = b"".join(pcm_chunks)
                logger.info(
                    "LocalAgentProtocol：使用外部 PCM 走 STT，字节=%d", len(pcm_total)
                )
                self._pipeline_task = asyncio.create_task(
                    self._run_agent_pipeline_pcm(pcm_total, stream_stt=stream_stt),
                    name="local-agent-pipeline-pcm",
                )
            else:
                if stream_stt is not None:
                    asyncio.create_task(stream_stt.abort())
                if frames:
                    self._pipeline_task = asyncio.create_task(
                        self._run_agent_pipeline(frames),
                        name="local-agent-pipeline",
                    )
                else:
                    logger.info("LocalAgentProtocol：无音频帧，跳过 STT pipeline")
                    self._fire_json({"type": "tts", "state": "stop"})

    def _schedule_auto_stop(self, reason: str):
        if self._auto_stop_task and not self._auto_stop_task.done():
            return
        self._auto_stop_task = asyncio.create_task(
            self._finish_listening(reason),
            name=f"local-auto-stop:{reason}",
        )

    def _process_auto_stop_frame(self, opus_frame: bytes):
        if not self._should_auto_stop():
            return

        try:
            decoder = self._get_vad_decoder()
            pcm_data = decoder.decode(opus_frame, AudioConfig.INPUT_FRAME_SIZE)
        except Exception as e:
            logger.debug("LocalAgentProtocol：自动断句解码失败: %s", e)
            return

        if self._is_speech_frame(pcm_data):
            self._speech_started = True
            self._speech_frames += 1
            self._silence_frames = 0
            return

        if not self._speech_started:
            return

        self._silence_frames += 1

        if len(self._opus_buffer) >= int(
            self._max_listen_seconds * 1000 / self._frame_duration_ms
        ):
            self._schedule_auto_stop("max_listen_reached")
            return

        if (
            self._speech_frames >= self._min_speech_frames
            and self._silence_frames >= self._silence_limit_frames
        ):
            self._schedule_auto_stop("vad_silence")

    # ─────────────────────────────────────────────────────────────────
    # Protocol 抽象方法实现
    # ─────────────────────────────────────────────────────────────────
    def is_audio_channel_opened(self) -> bool:
        return self._audio_channel_open

    async def open_audio_channel(self) -> bool:
        """
        "打开"本地音频通道：模拟服务器握手完成，不阻塞等待模型下载。
        模型预热在后台异步进行，不影响通道就绪时间。
        """
        if self._audio_channel_open:
            return True

        logger.info("LocalAgentProtocol：初始化本地音频通道…")

        try:
            self._audio_channel_open = True

            # 模拟服务端 hello 握手完成
            if self._on_incoming_json:
                self._on_incoming_json(
                    {
                        "type": "hello",
                        "version": 1,
                        "session_id": self.session_id,
                        "transport": "local",
                    }
                )

            # 触发音频通道打开回调
            if self._on_audio_channel_opened:
                await self._on_audio_channel_opened()

            # 后台预热模型（不阻塞通道就绪，避免 12s 超时）
            asyncio.create_task(self._background_preload(), name="local-preload")

            logger.info("LocalAgentProtocol：本地音频通道已就绪")
            return True

        except Exception as e:
            logger.error(f"LocalAgentProtocol：初始化失败：{e}", exc_info=True)
            self._audio_channel_open = False
            return False

    async def _background_preload(self):
        """后台预热 STT/TTS/LLM，不阻塞主流程。"""
        try:
            loop = asyncio.get_event_loop()
            self._get_agent()
            self._get_tts()
            stt = self._get_stt()
            await loop.run_in_executor(None, stt.preload)
            logger.info("LocalAgentProtocol：模型预热完成")
        except Exception as e:
            logger.warning(f"LocalAgentProtocol：后台预热出现非致命错误：{e}")

    # soundcard 录音已移除，统一使用 AudioCodec 的 send_audio 回调采集麦克风数据

    async def close_audio_channel(self):
        logger.info("LocalAgentProtocol：关闭本地音频通道")
        self._audio_channel_open = False
        self._is_listening = False
        self._reset_listen_tracking()

        if self._auto_stop_task and not self._auto_stop_task.done():
            self._auto_stop_task.cancel()
            try:
                await self._auto_stop_task
            except asyncio.CancelledError:
                pass
        self._auto_stop_task = None

        # 取消正在进行的 pipeline
        if self._pipeline_task and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass

        if self._on_audio_channel_closed:
            await self._on_audio_channel_closed()

    async def send_audio(self, data: bytes):
        """AudioCodec 编码的 Opus 帧，在监听期间缓冲备用于 STT。"""
        if self._is_listening and data:
            self._opus_buffer.append(data)
            self._process_auto_stop_frame(data)

    def set_tts_remote_sink(self, sink: Callable, has_listeners: Callable) -> None:
        """注册平板 TTS 输出通道。

        sink: async (mp3_bytes) -> int  返回成功推送的客户端数
        has_listeners: () -> bool       是否有客户端连着
        """
        self._tts_remote_sink = sink
        self._tts_remote_has_listeners = has_listeners

    def set_tts_remote_text_sink(self, text_sink: Callable) -> None:
        """注册平板直连 TTS 的 JSON 下行通道（用于 tts_text 等控制帧）。

        text_sink: async (msg: dict) -> int  返回成功推送的客户端数
        """
        self._tts_remote_text_sink = text_sink

    def _is_tablet_direct_enabled(self) -> bool:
        if self._tablet_session_disabled:
            return False
        if not bool(self.config.get_config("TTS.tablet_direct", False)):
            return False
        if self._tts_remote_text_sink is None:
            return False
        if self._tts_remote_has_listeners is None or not self._tts_remote_has_listeners():
            return False
        return True

    def _reset_tablet_session_state(self) -> None:
        """新一轮对话开始时清空平板直连的临时状态。"""
        self._tablet_session_disabled = False
        self._tablet_consecutive_fail = 0
        self._tablet_failed_handled.clear()
        self._tablet_segment_text.clear()

    async def on_tablet_audio_out_text(self, msg: dict) -> None:
        """处理平板上行的 JSON：当前只处理 tts_failed。"""
        if not isinstance(msg, dict):
            return
        mtype = msg.get("type")
        if mtype != "tts_failed":
            logger.debug("[TTS/direct] 收到未知上行 type=%s", mtype)
            return

        seg_id = msg.get("segment_id")
        reason = msg.get("reason", "unknown")
        text = msg.get("text") or self._tablet_segment_text.get(seg_id, "")
        logger.warning(
            "[TTS/direct] 平板报告合成失败 seg=%s reason=%s 文本=%r",
            seg_id, reason, (text or "")[:40],
        )

        if seg_id in self._tablet_failed_handled:
            logger.info("[TTS/direct] seg=%s 已 fallback 过，忽略重复", seg_id)
            return
        if seg_id is not None:
            self._tablet_failed_handled.add(seg_id)

        # 累加连续失败 → 触发冷却
        self._tablet_consecutive_fail += 1
        threshold = int(self.config.get_config("TTS.tablet_fallback_cooldown_count", 3))
        if self._tablet_consecutive_fail >= threshold:
            self._tablet_session_disabled = True
            logger.warning(
                "[TTS/direct] 连续 %d 段失败，本轮对话剩余段降级走 mp3 旧路径",
                self._tablet_consecutive_fail,
            )

        if not text or not text.strip():
            logger.info("[TTS/direct] seg=%s 无文本，无法 fallback", seg_id)
            return
        if self._tts_remote_sink is None:
            logger.warning("[TTS/direct] 无 mp3 sink，无法 fallback")
            return

        # 走旧路径补合成
        try:
            mp3, first_chunk_ms = await self._synthesize_mp3_for_remote_timed(text)
            if not mp3:
                logger.warning("[TTS/direct] fallback 合成为空 seg=%s", seg_id)
                return
            n = await self._tts_remote_sink(mp3)
            logger.info(
                "[TTS/direct] fallback ok seg=%s 首块=%.0fms 字数=%d 客户端=%d",
                seg_id, first_chunk_ms, len(text), n,
            )
        except Exception as e:
            logger.error("[TTS/direct] fallback 推送异常 seg=%s: %s", seg_id, e, exc_info=True)

    async def feed_external_pcm(self, pcm_bytes: bytes) -> None:
        """
        接收外部 PCM 输入（16kHz / 16-bit / 单声道）。

        来源例如平板 WebView 通过 /ws/audio_in 推上来的原生录音流。
        仅在 _is_listening 为 True 时缓冲；松开按钮时由 _finish_listening
        优先消费此 buffer 走 STT，跳过 Opus 编解码。
        """
        if self._is_listening and pcm_bytes:
            self._external_pcm_chunks.append(pcm_bytes)
            if self._stream_stt_active and self._stream_stt is not None:
                # 顺序 await 保证 WS 帧不乱序；缓冲保留作 fallback 兜底
                await self._stream_stt.feed(pcm_bytes)

    async def send_text(self, message: str):
        """
        拦截应用层发送的协议消息，路由本地行为。

        注意：此处"发送"对于本地 Agent 意味着"本地处理"，
        不存在任何网络发送。
        """
        try:
            data = json.loads(message) if isinstance(message, str) else message
        except json.JSONDecodeError:
            logger.warning(f"LocalAgentProtocol.send_text: 无法解析 JSON：{message!r}")
            return

        msg_type = data.get("type")
        state = data.get("state")

        if msg_type == "listen":
            if state == "start":
                logger.info("LocalAgentProtocol：开始录音缓冲")
                self._listen_mode = str(data.get("mode") or "manual")
                self._is_listening = True
                self._opus_buffer.clear()
                self._external_pcm_chunks.clear()
                self._reset_listen_tracking()
                # 录音的同时预热 qwen-flash：把 STT 那段时间填满，
                # 等 STT 出文本时 LLM 连接已经热好
                self._kick_llm_fast_warmup()
                # 流式 STT 会话：开关打开则后台建立，建立失败自动降级
                self._stream_stt_active = False
                if self._is_stream_stt_enabled():
                    asyncio.create_task(self._kick_stream_stt_start())

            elif state == "stop":
                logger.info("LocalAgentProtocol：停止录音，触发 Agent 流水线")
                await self._finish_listening("explicit_stop")

            elif state == "detect":
                wake_text = data.get("text", "").strip()
                logger.info(f"LocalAgentProtocol：收到文字输入/唤醒词 '{wake_text}'")
                # 如果有实际文字内容（来自 UI 文字输入框），直接运行文字 pipeline，跳过 STT
                if wake_text:
                    if self._pipeline_task and not self._pipeline_task.done():
                        self._pipeline_task.cancel()
                    self._pipeline_task = asyncio.create_task(
                        self._run_text_pipeline(wake_text),
                        name="local-text-pipeline",
                    )

        elif msg_type == "abort":
            logger.info("LocalAgentProtocol：收到中断指令，取消 Agent 流水线")
            if self._pipeline_task and not self._pipeline_task.done():
                self._pipeline_task.cancel()
            # 通知 Application 回到 IDLE
            if self._on_incoming_json:
                self._on_incoming_json({"type": "tts", "state": "stop"})

        elif msg_type == "iot":
            # IoT 状态/描述符上报：本地 Agent 模式下直接透传回 Application
            # （IoTPlugin 会处理 descriptors/states 更新）
            logger.debug(f"LocalAgentProtocol：透传 IoT 消息 state={state}")
            # No-op：IoTPlugin 通过 on_incoming_json 接收来自"服务器"的 iot 命令
            # 此处是客户端向服务端上报状态，本地模式下可以忽略

        elif msg_type == "mcp":
            # 本地模式：LLM Agent 直接调用 MCP 工具，无需 JSON-RPC 消息传递
            logger.debug("LocalAgentProtocol：MCP 消息在本地模式下由 Agent 直接处理，已忽略")

        else:
            logger.debug(f"LocalAgentProtocol：忽略消息 type={msg_type}")

    # ─────────────────────────────────────────────────────────────────
    # Agent 流水线（核心）
    # ─────────────────────────────────────────────────────────────────

    def _audio_output_available(self) -> bool:
        """检查音频输出是否可用：优先 AudioCodec，其次 PulseAudio。"""
        # 方式 1：AudioCodec output_stream
        try:
            from src.application import Application
            app = Application.get_instance()
            codec = getattr(app, "audio_codec", None)
            if codec is not None:
                if getattr(codec, "_output_stream", None) is not None or \
                   getattr(codec, "output_stream", None) is not None:
                    return True
        except Exception:
            pass
        # 方式 2：PulseAudio 可用
        try:
            import subprocess
            r = subprocess.run(["pactl", "info"], capture_output=True, timeout=2)
            if r.returncode == 0:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _clean_text_for_tts(text: str) -> str:
        """清理文本中不适合语音朗读的内容：emoji、markdown 标记、装饰符号等。"""
        import re as _re
        # 移除 emoji 及其他特殊 Unicode 符号
        text = _re.sub(
            r"[\U0001F600-\U0001F64F"   # emoticons
            r"\U0001F300-\U0001F5FF"    # symbols & pictographs
            r"\U0001F680-\U0001F6FF"    # transport & map
            r"\U0001F900-\U0001F9FF"    # supplemental symbols
            r"\U0001FA00-\U0001FA6F"    # chess symbols
            r"\U0001FA70-\U0001FAFF"    # symbols extended-A
            r"\U00002702-\U000027B0"    # dingbats
            r"\U0000FE00-\U0000FE0F"    # variation selectors
            r"\U0000200D"               # zero width joiner
            r"\U00002600-\U000026FF"    # misc symbols
            r"\U0000231A-\U0000231B"
            r"\U00002934-\U00002935"
            r"\U000025AA-\U000025FE"
            r"\U00002B05-\U00002B55"
            r"\U00003030\U0000303D"
            r"\U00003297\U00003299"
            r"]+", "", text)
        # 先剥 markdown 粗体/斜体（成对出现）
        text = _re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
        text = _re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", text)
        # 移除 markdown 标题标记
        text = _re.sub(r"^#{1,6}\s*", "", text, flags=_re.MULTILINE)
        # 移除 markdown 链接，保留文字
        text = _re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        # 移除 markdown 代码块标记
        text = _re.sub(r"```[^\n]*\n?", "", text)
        text = _re.sub(r"`([^`]+)`", r"\1", text)
        # 兜底剥未配对的 markdown 强调标记 (流式按段切分时容易残留)
        text = _re.sub(r"\*+", "", text)
        text = _re.sub(r"_+", " ", text)
        # 移除装饰性符号：全角波浪号、破折号、项目符号、几何图形、CJK 装饰等
        text = _re.sub(
            r"[‐-―"           # 各种 dash / em-dash / en-dash
            r"•‣⁃∙" # bullet 类
            r"■-◿"            # 几何形状（▪▫■□●○等）
            r"★-☆"            # ★☆
            r"〰〜～"       # 波浪号 ～〜
            r"︰-﹏"            # CJK 兼容装饰
            r"]+", "", text)
        # 移除多余空行
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _take_complete_segment(
        self, buffer: str, force_max_len: int = 60
    ) -> Optional[tuple]:
        """
        从增长中的 token 缓冲里切出一个可以送 TTS 的完整段。

        优先在强终止符（。！？!?；;\n）处切；
        当缓冲超过 force_max_len 仍无强标点时，退而求其次在最近一个 (，,、) 处切；
        都没有则返回 None，等更多 token。

        返回 (segment, remaining_buffer) 或 None。
        """
        if not buffer:
            return None
        strong = "。！？!?；;\n"
        for i, ch in enumerate(buffer):
            if ch in strong:
                return buffer[: i + 1], buffer[i + 1 :]
        if len(buffer) >= force_max_len:
            soft = "，,、 "
            for i in range(len(buffer) - 1, -1, -1):
                if buffer[i] in soft:
                    return buffer[: i + 1], buffer[i + 1 :]
            return buffer, ""
        return None

    async def _tts_sink_one_segment(self, seg: str) -> bool:
        """
        合成单段并推到远端 sink。返回是否成功推送。
        分两条路径：
          - TTS.tablet_direct=true 且平板已连：发 tts_text JSON，平板自合成
          - 否则：开发板合成 mp3 → 推 audio_out 二进制
        """
        seg = self._clean_text_for_tts(seg)
        if not seg.strip():
            return False
        import re as _re
        if not _re.search(r"[一-鿿0-9A-Za-z]", seg):
            logger.info("[TTS/stream] 段无可朗读内容，跳过 seg=%r", seg)
            return False

        # 路径 A：平板直连（JSON 下行，平板自己 fetch 云 TTS）
        if self._is_tablet_direct_enabled():
            seg_id = self._tablet_segment_id
            self._tablet_segment_id += 1
            self._tablet_segment_text[seg_id] = seg
            voice = str(self.config.get_config("TTS.qwen_voice", "Cherry"))
            msg = {
                "type": "tts_text",
                "segment_id": seg_id,
                "text": seg,
                "voice": voice,
            }
            try:
                push_t0 = time.perf_counter()
                n = await self._tts_remote_text_sink(msg)
                push_ms = (time.perf_counter() - push_t0) * 1000
                if n <= 0:
                    logger.info("[TTS/direct] seg=%d 无客户端接收，回退 mp3 路径", seg_id)
                    # 没人接收 → 直接回退本段
                    self._tablet_segment_text.pop(seg_id, None)
                else:
                    logger.info(
                        "[TTS/direct] seg=%d ok 字数=%d 推送=%.0fms 客户端=%d",
                        seg_id, len(seg), push_ms, n,
                    )
                    return True
            except Exception as e:
                logger.warning("[TTS/direct] seg=%d 下行失败: %s, 回退 mp3 路径", seg_id, e)
                self._tablet_segment_text.pop(seg_id, None)

        # 路径 B：旧的 mp3 推流（兜底）
        if self._tts_remote_sink is None or self._tts_remote_has_listeners is None:
            return False
        if not self._tts_remote_has_listeners():
            return False
        try:
            t0 = time.perf_counter()
            mp3, first_chunk_ms = await self._synthesize_mp3_for_remote_timed(seg)
            syn_ms = (time.perf_counter() - t0) * 1000
            if not mp3:
                logger.warning("[TTS/stream] 段合成为空 字数=%d 耗时=%.0fms", len(seg), syn_ms)
                return False
            push_t0 = time.perf_counter()
            n = await self._tts_remote_sink(mp3)
            push_ms = (time.perf_counter() - push_t0) * 1000
            logger.info(
                "[TTS/stream] 段 ok 字数=%d 首块=%.0fms 合成=%.0fms 推送=%.0fms 客户端=%d",
                len(seg), first_chunk_ms, syn_ms, push_ms, n,
            )
            return True
        except Exception as e:
            logger.warning("[TTS/stream] 段推送失败: %s", e)
            return False

    def _split_for_tts(self, text: str, max_len: int = 40) -> List[str]:
        """
        把回复切成短句以便边合成边推流。
        先按强终止符 (。！？!?；;\n) 切；过长的段再按 (，,、) 二次切。
        """
        import re
        parts: List[str] = []
        for chunk in re.split(r'(?<=[。！？!?；;\n])', text):
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(chunk) <= max_len:
                parts.append(chunk)
                continue
            for sub in re.split(r'(?<=[，,、])', chunk):
                sub = sub.strip()
                if sub:
                    parts.append(sub)
        return parts or ([text.strip()] if text.strip() else [])

    async def _synthesize_mp3_for_remote(self, text: str) -> bytes:
        """合成完整 mp3 字节用于推到平板播放。当前用 edge-tts。"""
        mp3, _ = await self._synthesize_mp3_for_remote_timed(text)
        return mp3

    def _kick_edge_tts_warmup(self) -> None:
        """
        预热 edge-tts：异步起一次极短合成把 TLS+WSS 握手做掉。
        和 LLM 推理并行，命中实际合成时连接是热的。
        edge-tts 不复用底层连接，所以仅对接下来一小段时间内的首次合成有效。
        """
        provider = self._get_tts_provider_name()
        if provider not in {"edge", "edge_tts", "edge-tts"}:
            logger.info("[TTS/warmup] 跳过：provider=%s 非 edge", provider)
            return
        if self._tts_remote_has_listeners is None:
            logger.info("[TTS/warmup] 跳过：has_listeners 回调未注册")
            return
        if not self._tts_remote_has_listeners():
            logger.info("[TTS/warmup] 跳过：暂无平板监听")
            return
        if self._tts_warmup_task and not self._tts_warmup_task.done():
            logger.info("[TTS/warmup] 跳过：已有预热任务在跑")
            return

        async def _warmup():
            try:
                import edge_tts
                voice = self.config.get_config("TTS.voice", "zh-CN-XiaoxiaoNeural")
                t0 = time.perf_counter()
                # edge-tts 对纯标点会返回 "No audio received"，预热文本必须有可朗读字符
                communicate = edge_tts.Communicate("嗯。", voice)
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio":
                        break
                logger.info("[TTS/warmup] edge-tts 预热完成 %.0fms",
                            (time.perf_counter() - t0) * 1000)
            except Exception as e:
                logger.warning("[TTS/warmup] 预热失败: %s", e)

        try:
            self._tts_warmup_task = asyncio.create_task(_warmup(), name="edge-tts-warmup")
            logger.info("[TTS/warmup] 已触发预热任务")
        except RuntimeError as e:
            logger.warning("[TTS/warmup] create_task 失败: %s", e)

    def _kick_llm_fast_warmup(self) -> None:
        """
        预热 qwen-flash：在用户开始录音时跑一发极小的 chat completion，
        把 TLS+连接池准备好；等 STT 出来时连接还热。
        4s 内重复触发会被合并。
        """
        if self._llm_fast_warmup_task and not self._llm_fast_warmup_task.done():
            return
        now_ms = time.perf_counter() * 1000
        if now_ms - self._llm_fast_last_warmup_ms < 4000:
            return
        self._llm_fast_last_warmup_ms = now_ms

        async def _warmup():
            try:
                from src.llm.llm_client import LLMClient
                if not getattr(self, "_llm_fast", None):
                    self._llm_fast = LLMClient(config_section="LLM_FAST")
                t0 = time.perf_counter()
                stream = await self._llm_fast.chat_completion(
                    messages=[{"role": "user", "content": "嗨"}],
                    tools=None, stream=True,
                )
                got_first = False
                async for chunk in stream:
                    if getattr(chunk, "choices", None):
                        got_first = True
                        break
                try:
                    await stream.aclose()
                except Exception:
                    pass
                logger.info(
                    "[LLM/warmup] qwen-flash 预热完成 %.0fms (首token=%s)",
                    (time.perf_counter() - t0) * 1000, got_first,
                )
            except Exception as e:
                logger.warning("[LLM/warmup] 失败: %s", e)

        try:
            self._llm_fast_warmup_task = asyncio.create_task(
                _warmup(), name="llm-fast-warmup"
            )
            logger.info("[LLM/warmup] 已触发 qwen-flash 预热")
        except RuntimeError as e:
            logger.warning("[LLM/warmup] create_task 失败: %s", e)

    async def _synthesize_mp3_for_remote_timed(self, text: str):
        """带耗时埋点的远端 mp3 合成。返回 (mp3_bytes, 首个 audio 块到达耗时_ms)。"""
        provider = self._get_tts_provider_name()
        if provider in {"qwen", "qwen_tts", "qwen-tts"}:
            from src.tts.qwen_tts_client import QwenTTSClient
            if not getattr(self, "_qwen_tts_client", None):
                self._qwen_tts_client = QwenTTSClient()
            try:
                mp3, first_chunk_ms = await self._qwen_tts_client.synthesize_to_mp3(text)
                return mp3, first_chunk_ms
            except Exception as e:
                logger.warning("[TTS/remote] qwen-tts 合成失败: %s", e)
                return b"", -1.0

        import edge_tts
        from src.utils.config_manager import ConfigManager

        config = ConfigManager.get_instance()
        voice = config.get_config("TTS.voice", "zh-CN-XiaoxiaoNeural")
        rate = config.get_config("TTS.rate", "+0%")

        chunks: List[bytes] = []
        first_chunk_ms = -1.0
        t0 = time.perf_counter()
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    if first_chunk_ms < 0:
                        first_chunk_ms = (time.perf_counter() - t0) * 1000
                    chunks.append(chunk["data"])
        except Exception as e:
            logger.warning("[TTS/remote] edge-tts 合成失败: %s", e)
            return b"", first_chunk_ms
        return b"".join(chunks), first_chunk_ms

    async def _play_tts_pulseaudio(self, text: str):
        """通过 edge-tts 流式生成音频并实时喂给 paplay，减少首字节延迟。"""
        import edge_tts
        from src.utils.config_manager import ConfigManager
        config = ConfigManager.get_instance()
        voice = config.get_config("TTS.voice", "zh-CN-XiaoxiaoNeural")
        rate  = config.get_config("TTS.rate",  "+0%")

        # 启动 ffmpeg 管道：stdin 接收 mp3 流，stdout 输出 raw pcm
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", "pipe:0",
            "-f", "s16le", "-ar", "24000", "-ac", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # 启动 paplay 管道：stdin 接收 raw pcm
        paplay = await asyncio.create_subprocess_exec(
            "paplay", "--raw", "--format=s16le",
            "--rate=24000", "--channels=1",
            "/dev/stdin",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def _feed_mp3_to_ffmpeg():
            """流式将 edge-tts 音频块写入 ffmpeg stdin。"""
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio" and chunk["data"]:
                        ffmpeg.stdin.write(chunk["data"])
                        await ffmpeg.stdin.drain()
            except Exception as e:
                logger.warning(f"[TTS/stream] edge-tts 流式写入异常：{e}")
            finally:
                try:
                    ffmpeg.stdin.close()
                    await ffmpeg.stdin.wait_closed()
                except Exception:
                    pass

        async def _pipe_pcm_to_paplay():
            """从 ffmpeg stdout 读取 pcm 并实时写入 paplay。"""
            try:
                while True:
                    pcm_chunk = await ffmpeg.stdout.read(4096)
                    if not pcm_chunk:
                        break
                    paplay.stdin.write(pcm_chunk)
                    await paplay.stdin.drain()
            except Exception as e:
                logger.warning(f"[TTS/stream] pcm 转发异常：{e}")
            finally:
                try:
                    paplay.stdin.close()
                    await paplay.stdin.wait_closed()
                except Exception:
                    pass

        try:
            logger.info("[TTS/paplay] 启动 edge-tts → ffmpeg → paplay 管道")
            # 并行：edge-tts→ffmpeg 和 ffmpeg→paplay
            await asyncio.gather(
                _feed_mp3_to_ffmpeg(),
                _pipe_pcm_to_paplay(),
            )
            logger.info("[TTS/paplay] 数据流完成，等待 paplay 退出")
            await paplay.wait()
            logger.info("[TTS/paplay] paplay 已退出, returncode=%s", paplay.returncode)
        except Exception as e:
            logger.warning(f"[TTS/paplay] 流式播放失败：{e}")
        finally:
            for p in (ffmpeg, paplay):
                if p.returncode is None:
                    try:
                        p.kill()
                    except Exception:
                        pass

    async def _run_text_pipeline(self, user_text: str):
        """
        文字输入直达 LLM pipeline：跳过 STT，直接推理，TTS 可选。
        适用于：用户在 UI 输入框打字、WSL 无麦克风环境。
        """
        self._reset_tablet_session_state()
        try:
            # 切换到 SPEAKING 状态并显示用户输入
            self._fire_json({"type": "tts", "state": "start"})
            self._fire_json({"type": "stt", "state": "stop", "text": user_text})

            # 与 LLM 推理并行预热 edge-tts
            self._kick_edge_tts_warmup()

            mcp_server = self._get_mcp_server()
            tools = None if self._should_skip_tools_for_text(user_text) else mcp_server.get_openai_tools()
            agent = self._get_agent()

            logger.info(
                f"[Pipeline] 向 LLM 传入 {len(tools) if tools else 0} 个工具，用户输入='{user_text}'"
            )

            if self._can_use_streaming_remote_tts():
                reply = await self._consume_llm_stream_to_remote_tts(
                    agent, user_text, tools, mcp_server.execute_tool
                )
                if not reply:
                    reply = "（无回复）"
                logger.info(f"LLM 回复：'{reply[:100]}{'...' if len(reply) > 100 else ''}'")
                self._fire_json({"type": "tts", "state": "sentence_start", "text": reply})
            else:
                reply = await agent.run(
                    user_input=user_text,
                    tools=tools if tools else None,
                    tool_executor=mcp_server.execute_tool,
                )
                if not reply:
                    reply = "（无回复）"
                logger.info(f"LLM 回复：'{reply[:100]}{'...' if len(reply) > 100 else ''}'")
                self._fire_json({"type": "tts", "state": "sentence_start", "text": reply})
                # TTS：AudioCodec 路径 → paplay 路径 → 仅显示文字
                await self._play_tts_any(reply)

            self._fire_json({"type": "tts", "state": "stop"})
            logger.info("文字 pipeline 完成")

        except asyncio.CancelledError:
            self._fire_json({"type": "tts", "state": "stop"})
            raise
        except Exception as e:
            logger.error(f"文字 pipeline 异常：{e}", exc_info=True)
            self._fire_json({"type": "tts", "state": "stop"})

    def _can_use_streaming_remote_tts(self) -> bool:
        """是否走流式 LLM + 句级远端 TTS 路径。"""
        if self._tts_remote_sink is None or self._tts_remote_has_listeners is None:
            return False
        if not self._tts_remote_has_listeners():
            return False
        if self._get_tts_provider_name() not in {
            "edge", "edge_tts", "edge-tts",
            "qwen", "qwen_tts", "qwen-tts",
        }:
            return False
        return True

    async def _run_agent_pipeline_after_stt(self, user_text: str) -> None:
        """STT 完成之后的公共部分：三层路由 (Tier 0 关键词 → Tier 1 flash → Tier 2 fallback)。"""
        self._reset_tablet_session_state()
        if not user_text:
            logger.info("STT 未识别到有效内容，流水线终止")
            self._fire_json({"type": "stt", "state": "stop", "text": ""})
            self._fire_json({"type": "tts", "state": "stop"})
            return

        self._fire_json({"type": "stt", "state": "stop", "text": user_text})
        logger.info(f"STT 结果：'{user_text}'")

        self._fire_json({"type": "tts", "state": "start"})

        try:
            # ── Tier 0：关键词意图直达，0 LLM ─────────────────
            from src.protocols.intent_matcher import match_intent, match_tier2_direct

            fast_path_enabled = bool(
                self.config.get_config("ROUTER.fast_path_enabled", True)
            )

            if fast_path_enabled:
                hit = match_intent(user_text)
                if hit is not None:
                    await self._run_tier0_intent(user_text, hit)
                    return

            # ── Tier 2 直达：高级语义关键词跳过 Tier 1 ────────
            if fast_path_enabled and match_tier2_direct(user_text):
                await self._run_tier2_full(user_text, reason="tier2-direct")
                return

            # ── Tier 1：qwen-flash 闲聊（无工具），失败回退 ───
            if fast_path_enabled and self._can_use_streaming_remote_tts():
                tier1_ok = await self._run_tier1_fast(user_text)
                if tier1_ok:
                    return
                logger.info("[Tier1] 触发 fallback，转 Tier 2")

            # ── Tier 2：qwen3-coder-next + 完整工具（兜底） ──
            await self._run_tier2_full(user_text, reason="tier2-fallback-or-default")

        finally:
            self._fire_json({"type": "tts", "state": "stop"})
            logger.info("Agent 流水线完成")

    async def _run_tier0_intent(self, user_text: str, hit) -> None:
        """Tier 0：命中关键词直接调 MCP 工具 + ack TTS + 异步补对话历史。"""
        mcp_server = self._get_mcp_server()
        agent = self._get_agent()

        # 命令型：先 ack 再异步执行工具（用户先听到反馈）
        # 查询型（ack="")：先执行工具拿结果再播报
        if hit.ack:
            self._fire_json({"type": "tts", "state": "sentence_start", "text": hit.ack})
            self._kick_edge_tts_warmup()
            await self._tts_sink_one_segment(hit.ack)

            async def _exec_and_remember():
                try:
                    result = await mcp_server.execute_tool(hit.tool, hit.args)
                    logger.info("[Tier0] 工具 %s 完成: %s", hit.tool, str(result)[:200])
                except Exception as e:
                    logger.error("[Tier0] 工具 %s 执行失败: %s", hit.tool, e, exc_info=True)
                agent.remember_exchange(user_text, hit.ack)

            asyncio.create_task(_exec_and_remember(), name=f"tier0-exec:{hit.tool}")
        else:
            # 查询型：等结果
            try:
                raw = await mcp_server.execute_tool(hit.tool, hit.args)
                spoken = self._extract_tool_text(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
            except Exception as e:
                logger.error("[Tier0] 查询工具 %s 失败: %s", hit.tool, e, exc_info=True)
                spoken = "查询失败"
            self._fire_json({"type": "tts", "state": "sentence_start", "text": spoken})
            self._kick_edge_tts_warmup()
            await self._tts_sink_one_segment(spoken)
            agent.remember_exchange(user_text, spoken)

    async def _run_tier1_fast(self, user_text: str) -> bool:
        """
        Tier 1：qwen-flash 直接对话，无 tools，无 agent ReAct 循环。
        返回 True 表示完成（已推 TTS + 历史已写）；False 表示触发 fallback（未推任何 TTS）。
        """
        from src.llm.llm_client import LLMClient

        # 复用单例 fast client
        if not hasattr(self, "_llm_fast") or self._llm_fast is None:
            try:
                self._llm_fast = LLMClient(config_section="LLM_FAST")
            except Exception as e:
                logger.warning("[Tier1] LLM_FAST 客户端构造失败: %s，回退 Tier 2", e)
                return False

        agent = self._get_agent()
        history = agent.get_history()
        sys_prompt = self.config.get_config(
            "LLM_FAST.system_prompt",
            "你是物流终端机器人的语音助手，回答简短自然。"
        )
        messages = [{"role": "system", "content": sys_prompt}] + list(history) + [
            {"role": "user", "content": user_text}
        ]

        fallback_phrases = self.config.get_config(
            "ROUTER.fallback_phrases",
            ["做不了", "没办法处理", "我不会"],
        ) or []
        scan_chars = int(self.config.get_config("ROUTER.fallback_scan_chars", 30))

        # 与 LLM 推理并行预热 edge-tts
        self._kick_edge_tts_warmup()

        t0 = time.perf_counter()
        try:
            stream = await self._llm_fast.chat_completion(
                messages=messages, tools=None, stream=True,
            )
        except Exception as e:
            logger.warning("[Tier1] flash 请求失败 %s，回退 Tier 2", e)
            return False

        decided: Optional[str] = None  # None / "tier1" / "tier2"
        scan_buf = ""
        seg_buf = ""
        full = []
        first_seg_pushed_ms: Optional[float] = None

        try:
            async for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                token = getattr(delta, "content", None)
                if not token:
                    continue
                full.append(token)

                # 先做 fallback 探测
                if decided is None:
                    scan_buf += token
                    hit_phrase = next((p for p in fallback_phrases if p in scan_buf), None)
                    if hit_phrase:
                        logger.info(
                            "[Tier1] 命中 fallback 短语 %r (前 %d 字)，丢弃 flash 输出",
                            hit_phrase, len(scan_buf),
                        )
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        return False  # → Tier 2
                    if len(scan_buf) >= scan_chars:
                        decided = "tier1"
                        seg_buf = scan_buf
                        scan_buf = ""
                else:
                    seg_buf += token

                # 已确认 tier1 → 按段切句推 TTS
                if decided == "tier1":
                    while True:
                        cut = self._take_complete_segment(seg_buf)
                        if cut is None:
                            break
                        seg, seg_buf = cut
                        pushed = await self._tts_sink_one_segment(seg)
                        if pushed and first_seg_pushed_ms is None:
                            first_seg_pushed_ms = (time.perf_counter() - t0) * 1000
                            logger.info(
                                "[TTS/stream] 首段已推送 距 Tier1 起=%.0fms",
                                first_seg_pushed_ms,
                            )
        except Exception as e:
            logger.error("[Tier1] 流式异常: %s", e, exc_info=True)
            # 流式中途出错：若已推 TTS，吃下；否则让 Tier 2 接
            if decided != "tier1":
                return False

        # 流结束。如果 scan_buf 太短没决断，也算 tier1（极短回复，无 fallback 词）
        if decided is None and scan_buf.strip():
            decided = "tier1"
            seg_buf = scan_buf

        # 残余尾巴
        if decided == "tier1" and seg_buf.strip():
            pushed = await self._tts_sink_one_segment(seg_buf)
            if pushed and first_seg_pushed_ms is None:
                first_seg_pushed_ms = (time.perf_counter() - t0) * 1000
                logger.info(
                    "[TTS/stream] 首段(尾)已推送 距 Tier1 起=%.0fms",
                    first_seg_pushed_ms,
                )

        reply = "".join(full).strip()
        if not reply:
            return False
        logger.info(
            "[Tier1] flash 完成 总耗时=%.0fms 字数=%d 回复='%s%s'",
            (time.perf_counter() - t0) * 1000, len(reply),
            reply[:80], "..." if len(reply) > 80 else "",
        )
        self._fire_json({"type": "tts", "state": "sentence_start", "text": reply})
        # 历史补写（同步即可，写得起）
        self._get_agent().remember_exchange(user_text, reply)
        return True

    async def _run_tier2_full(self, user_text: str, reason: str) -> None:
        """Tier 2：原 coder-next + 完整 tools 路径。"""
        logger.info("[Tier2] 触发原因=%s", reason)

        # 与 LLM 推理并行预热 edge-tts
        self._kick_edge_tts_warmup()

        mcp_server = self._get_mcp_server()
        tools = (
            None
            if self._should_skip_tools_for_text(user_text)
            else mcp_server.get_openai_tools()
        )
        agent = self._get_agent()

        if self._can_use_streaming_remote_tts():
            reply = await self._consume_llm_stream_to_remote_tts(
                agent, user_text, tools, mcp_server.execute_tool
            )
            if not reply:
                reply = "（无回复）"
            logger.info(f"[Tier2] LLM 回复：'{reply[:100]}{'...' if len(reply) > 100 else ''}'")
            self._fire_json({"type": "tts", "state": "sentence_start", "text": reply})
        else:
            reply = await agent.run(
                user_input=user_text,
                tools=tools if tools else None,
                tool_executor=mcp_server.execute_tool,
            )
            if not reply:
                reply = "（无回复）"
            logger.info(f"[Tier2] LLM 回复：'{reply[:100]}{'...' if len(reply) > 100 else ''}'")
            self._fire_json({"type": "tts", "state": "sentence_start", "text": reply})
            await self._play_tts_any(reply)

    async def _consume_llm_stream_to_remote_tts(
        self,
        agent,
        user_text: str,
        tools,
        tool_executor,
    ) -> str:
        """
        消费 agent.run_streaming 的 token 流，按强标点切句即时推 TTS。
        返回完整 reply 文本（用于日志 / UI 渲染）。
        """
        t0 = time.perf_counter()
        first_seg_ms: Optional[float] = None
        full = []
        buffer = ""
        try:
            async for token in agent.run_streaming(
                user_input=user_text,
                tools=tools if tools else None,
                tool_executor=tool_executor,
            ):
                if not token:
                    continue
                full.append(token)
                buffer += token
                while True:
                    cut = self._take_complete_segment(buffer)
                    if cut is None:
                        break
                    seg, buffer = cut
                    pushed = await self._tts_sink_one_segment(seg)
                    if pushed and first_seg_ms is None:
                        first_seg_ms = (time.perf_counter() - t0) * 1000
                        logger.info("[TTS/stream] 首段已推送 距 LLM 起=%.0fms", first_seg_ms)
            # 残余尾巴
            if buffer.strip():
                pushed = await self._tts_sink_one_segment(buffer)
                if pushed and first_seg_ms is None:
                    first_seg_ms = (time.perf_counter() - t0) * 1000
                    logger.info("[TTS/stream] 首段(尾)已推送 距 LLM 起=%.0fms", first_seg_ms)
        except Exception as e:
            logger.error("[TTS/stream] 流式管线异常：%s", e, exc_info=True)
        return "".join(full)

    async def _run_agent_pipeline_pcm(self, pcm_bytes: bytes, stream_stt=None):
        """外部 PCM 输入版本：跳过 Opus 解码，直接走 STT。

        优先用流式 STT 拿最终文本；任一失败/超时降级到批量 transcribe_pcm。
        """
        try:
            self._fire_json({"type": "stt", "state": "start"})

            user_text: Optional[str] = None
            if stream_stt is not None:
                timeout_ms = float(
                    self.config.get_config("STT.streaming_finish_timeout_ms", 1500)
                )
                t0 = time.perf_counter()
                user_text = await stream_stt.finish(timeout=timeout_ms / 1000.0)
                dt = (time.perf_counter() - t0) * 1000
                if user_text is None:
                    logger.warning("[stream-stt] finish 失败/超时，降级走批量 STT")
                else:
                    logger.info("[stream-stt] finish 完成 %.0fms 字数=%d", dt, len(user_text))

            if user_text is None:
                stt = self._get_stt()
                transcribe_pcm = getattr(stt, "transcribe_pcm", None)
                if transcribe_pcm is None:
                    logger.warning(
                        "当前 STT provider 不支持 transcribe_pcm，回退跳过外部 PCM"
                    )
                    self._fire_json({"type": "stt", "state": "stop", "text": ""})
                    self._fire_json({"type": "tts", "state": "stop"})
                    return
                user_text = await transcribe_pcm(pcm_bytes)
            await self._run_agent_pipeline_after_stt(user_text)
        except asyncio.CancelledError:
            logger.info("Agent 流水线(PCM) 被取消")
            self._fire_json({"type": "tts", "state": "stop"})
            raise
        except Exception as e:
            logger.error(f"Agent 流水线(PCM) 异常：{e}", exc_info=True)
            self._fire_json({"type": "tts", "state": "stop"})

    async def _run_agent_pipeline(self, opus_frames: List[bytes]):
        """
        完整的 STT → LLM → TTS 流水线。

        此方法在独立 Task 中运行，可被 asyncio.CancelledError 中断。
        """
        try:
            # ── 1. STT：显示识别中 ─────────────────────────────────
            self._fire_json({"type": "stt", "state": "start"})

            stt = self._get_stt()
            user_text = await stt.transcribe(opus_frames)
            await self._run_agent_pipeline_after_stt(user_text)

        except asyncio.CancelledError:
            logger.info("Agent 流水线被取消")
            # 确保状态复位
            self._fire_json({"type": "tts", "state": "stop"})
            raise

        except Exception as e:
            logger.error(f"Agent 流水线异常：{e}", exc_info=True)
            # 尝试用 TTS 报告错误（仅在有音频设备时）
            if self._audio_output_available():
                try:
                    tts = self._get_tts()
                    error_frames = await tts.synthesize_to_opus_frames(f"抱歉，出现了错误：{e}")
                    for frame in error_frames:
                        if self._on_incoming_audio:
                            self._on_incoming_audio(frame)
                        await asyncio.sleep(0.020)
                except Exception:
                    pass
            self._fire_json({"type": "tts", "state": "stop"})

    # ─────────────────────────────────────────────────────────────────
    # 辅助
    # ─────────────────────────────────────────────────────────────────

    async def _play_tts_any(self, text: str):
        """
        TTS 播放三级降级：
          1. PulseAudio paplay（WSL2 mirrored 模式，优先）
          2. AudioCodec Opus 链路（原生声卡，回退）
          3. 仅显示文字（无任何音频设备）
        """
        logger.info("[TTS] _play_tts_any 入口, 原始长度=%d", len(text))
        # 清理不适合朗读的内容（emoji、markdown 等）
        text = self._clean_text_for_tts(text)
        logger.info("[TTS] 清理后长度=%d", len(text))
        if not text:
            logger.info("[TTS] 清理后无可朗读文本，跳过播放")
            return

        provider = self._get_tts_provider_name()
        logger.info("[TTS] provider=%s", provider)

        # 级别 0：推到平板（如果有平板连着 /ws/audio_out）
        if (
            self._tts_remote_sink is not None
            and self._tts_remote_has_listeners is not None
            and self._tts_remote_has_listeners()
        ):
            try:
                t0 = time.perf_counter()
                logger.info("[TTS] 检测到平板已连接，走远端推流路径(句级流式) t0=%.3f", t0)
                segments = self._split_for_tts(text)
                logger.info("[TTS/remote] 切成 %d 段, 切分耗时=%.0fms 内容=%s",
                            len(segments), (time.perf_counter() - t0) * 1000,
                            [s[:10] + ('…' if len(s) > 10 else '') for s in segments])
                pushed_any = False
                first_audible_ms = None
                for idx, seg in enumerate(segments):
                    seg_t0 = time.perf_counter()
                    mp3, first_chunk_ms = await self._synthesize_mp3_for_remote_timed(seg)
                    syn_ms = (time.perf_counter() - seg_t0) * 1000
                    if not mp3:
                        logger.warning("[TTS/remote] 段 %d 合成为空, 字数=%d, 耗时=%.0fms, 跳过",
                                       idx, len(seg), syn_ms)
                        continue
                    push_t0 = time.perf_counter()
                    n = await self._tts_remote_sink(mp3)
                    push_ms = (time.perf_counter() - push_t0) * 1000
                    total_ms = (time.perf_counter() - t0) * 1000
                    if first_audible_ms is None:
                        first_audible_ms = total_ms
                    logger.info(
                        "[TTS/remote] 段 %d ok 字数=%d 首块=%.0fms 合成总=%.0fms "
                        "推送=%.0fms 累计=%.0fms mp3=%dB 客户端=%d",
                        idx, len(seg), first_chunk_ms, syn_ms, push_ms, total_ms,
                        len(mp3), n,
                    )
                    pushed_any = True
                if pushed_any:
                    logger.info("[TTS/remote] 完成: 首段可听=%.0fms 总耗时=%.0fms 段数=%d",
                                first_audible_ms or -1,
                                (time.perf_counter() - t0) * 1000, len(segments))
                    return
                logger.warning("[TTS] 远端所有分段合成均为空，回退本地播放")
            except Exception as e:
                logger.warning(f"[TTS] 远端推流失败，回退本地: {e}")

        # 级别 1：PulseAudio paplay（仅 edge-tts 快捷路径）
        if provider in {"edge", "edge_tts", "edge-tts"}:
            try:
                import subprocess

                r = subprocess.run(["pactl", "info"], capture_output=True, timeout=2)
                logger.info("[TTS] pactl returncode=%d", r.returncode)
                if r.returncode == 0:
                    logger.info("[TTS] 走 PulseAudio 路径，开始合成")
                    await self._play_tts_pulseaudio(text)
                    logger.info("[TTS] PulseAudio 播放完成")
                    return
            except Exception as e:
                logger.warning(f"[TTS] PulseAudio 检测失败：{e}")

        # 级别 2：AudioCodec
        try:
            from src.application import Application
            app = Application.get_instance()
            codec = getattr(app, "audio_codec", None)
            if codec is not None and (
                getattr(codec, "_output_stream", None) is not None or
                getattr(codec, "output_stream", None) is not None
            ):
                tts = self._get_tts()
                opus_frames = await tts.synthesize_to_opus_frames(text)
                for frame in opus_frames:
                    await asyncio.sleep(0)
                    if self._on_incoming_audio:
                        self._on_incoming_audio(frame)
                    await asyncio.sleep(0.020)
                logger.info("[TTS] AudioCodec 播放完成")
                return
        except Exception as e:
            logger.warning(f"[TTS] AudioCodec 路径失败：{e}")

        # 级别 3：仅文字
        logger.info("[TTS] 无音频设备，跳过播放（文字已显示）")
        await asyncio.sleep(0.3)

    def _fire_json(self, msg: dict):
        """同步触发 on_incoming_json 回调（模拟服务端发消息给客户端）。"""
        if self._on_incoming_json:
            try:
                self._on_incoming_json(msg)
            except Exception as e:
                logger.debug(f"_fire_json 回调异常：{e}")
