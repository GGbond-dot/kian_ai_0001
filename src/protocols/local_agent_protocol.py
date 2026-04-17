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
from typing import List, Optional

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

        # 正在进行的 pipeline 任务（用于取消）
        self._pipeline_task: Optional[asyncio.Task] = None
        self._auto_stop_task: Optional[asyncio.Task] = None
        self._stop_lock = asyncio.Lock()

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
            self._opus_buffer.clear()
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
                self._reset_listen_tracking()

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
        """清理文本中不适合语音朗读的内容：emoji、markdown 标记等。"""
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
        # 移除 markdown 粗体/斜体标记
        text = _re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
        text = _re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", text)
        # 移除 markdown 标题标记
        text = _re.sub(r"^#{1,6}\s*", "", text, flags=_re.MULTILINE)
        # 移除 markdown 链接，保留文字
        text = _re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        # 移除 markdown 代码块标记
        text = _re.sub(r"```[^\n]*\n?", "", text)
        text = _re.sub(r"`([^`]+)`", r"\1", text)
        # 移除多余空行
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

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
            # 并行：edge-tts→ffmpeg 和 ffmpeg→paplay
            await asyncio.gather(
                _feed_mp3_to_ffmpeg(),
                _pipe_pcm_to_paplay(),
            )
            await paplay.wait()
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
        try:
            # 切换到 SPEAKING 状态并显示用户输入
            self._fire_json({"type": "tts", "state": "start"})
            self._fire_json({"type": "stt", "state": "stop", "text": user_text})

            mcp_server = self._get_mcp_server()
            tools = None if self._should_skip_tools_for_text(user_text) else mcp_server.get_openai_tools()
            agent = self._get_agent()

            logger.info(
                f"[Pipeline] 向 LLM 传入 {len(tools) if tools else 0} 个工具，用户输入='{user_text}'"
            )

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

            if not user_text:
                logger.info("STT 未识别到有效内容，流水线终止")
                self._fire_json({"type": "stt", "state": "stop", "text": ""})
                self._fire_json({"type": "tts", "state": "stop"})
                return

            self._fire_json({"type": "stt", "state": "stop", "text": user_text})
            logger.info(f"STT 结果：'{user_text}'")

            # ── 2. 切换到 SPEAKING 状态 ───────────────────────────
            self._fire_json({"type": "tts", "state": "start"})

            # ── 3. LLM Agent 推理 ─────────────────────────────────
            mcp_server = self._get_mcp_server()
            tools = None if self._should_skip_tools_for_text(user_text) else mcp_server.get_openai_tools()
            agent = self._get_agent()

            reply = await agent.run(
                user_input=user_text,
                tools=tools if tools else None,
                tool_executor=mcp_server.execute_tool,
            )

            if not reply:
                reply = "（无回复）"
            logger.info(f"LLM 回复：'{reply[:100]}{'...' if len(reply) > 100 else ''}'")

            # 发送 LLM 文字到 UI
            self._fire_json(
                {"type": "tts", "state": "sentence_start", "text": reply}
            )

            # ── 4. TTS：AudioCodec 路径 → paplay 路径 → 仅显示文字 ──
            await self._play_tts_any(reply)

            # ── 5. 通知播放结束 ───────────────────────────────────
            self._fire_json({"type": "tts", "state": "stop"})
            logger.info("Agent 流水线完成")

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
        # 清理不适合朗读的内容（emoji、markdown 等）
        text = self._clean_text_for_tts(text)
        if not text:
            logger.info("[TTS] 清理后无可朗读文本，跳过播放")
            return

        # 级别 1：PulseAudio paplay（仅 edge-tts 快捷路径）
        if self._get_tts_provider_name() in {"edge", "edge_tts", "edge-tts"}:
            try:
                import subprocess

                r = subprocess.run(["pactl", "info"], capture_output=True, timeout=2)
                if r.returncode == 0:
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
