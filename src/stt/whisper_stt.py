"""
Whisper STT (Speech-to-Text) 模块
使用 faster-whisper 在本地进行语音识别，无需联网。
"""
import asyncio
import io
import struct
from typing import List, Optional

import numpy as np

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class WhisperSTT:
    """
    基于 faster-whisper 的本地语音识别。

    接收 Opus 编码的音频帧列表，解码后送入 Whisper 模型转写为文字。
    采用懒加载策略，首次调用 transcribe() 时才真正加载模型。
    """

    def __init__(self):
        config = ConfigManager.get_instance()
        self._model_size = config.get_config("STT.model", "base")
        self._device = config.get_config("STT.device", "cpu")
        self._compute_type = config.get_config("STT.compute_type", "int8")
        self._language = config.get_config("STT.language", "zh")
        self._beam_size = int(config.get_config("STT.beam_size", 5))
        self._vad_filter = bool(config.get_config("STT.vad_filter", True))
        self._vad_min_silence_ms = int(
            config.get_config("STT.vad_min_silence_duration_ms", 500)
        )
        self._model = None  # 懒加载
        self._opus_decoder = None  # 懒加载
        logger.info(
            f"WhisperSTT 配置：model={self._model_size}, device={self._device}, "
            f"language={self._language}"
        )

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------
    def _load_model(self):
        """加载 faster-whisper 模型（懒加载，只执行一次）。"""
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel

            logger.info(
                f"正在加载 Whisper 模型 '{self._model_size}'，"
                f"设备={self._device}，精度={self._compute_type}…"
            )
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info("Whisper 模型加载完成")
        except ImportError:
            raise RuntimeError(
                "faster-whisper 未安装，请执行：pip install faster-whisper"
            )

    def _get_opus_decoder(self):
        """获取 Opus 解码器（懒加载）。"""
        if self._opus_decoder is not None:
            return self._opus_decoder
        try:
            import opuslib  # noqa: F401
        except ImportError:
            raise RuntimeError("opuslib 未安装，请执行：pip install opuslib")

        import opuslib

        # 16kHz 单声道，与 AudioCodec 编码侧一致
        self._opus_decoder = opuslib.Decoder(16000, 1)
        return self._opus_decoder

    def _opus_frames_to_float32(self, opus_frames: List[bytes]) -> np.ndarray:
        """
        将 Opus 帧列表解码为 float32 numpy 数组。

        每帧解码为 int16 PCM (16kHz, mono) 后转 float32 归一化到 [-1, 1]。
        """
        decoder = self._get_opus_decoder()
        pcm_chunks: List[bytes] = []

        # 每帧 20ms @ 16kHz = 320 samples
        frame_size = 320

        for frame in opus_frames:
            if not frame:
                continue
            try:
                pcm = decoder.decode(frame, frame_size)
                pcm_chunks.append(pcm)
            except Exception as e:
                logger.debug(f"Opus 解码单帧失败，已跳过：{e}")

        if not pcm_chunks:
            return np.array([], dtype=np.float32)

        raw = b"".join(pcm_chunks)
        # bytes → int16 array → float32 [-1, 1]
        num_samples = len(raw) // 2
        int16_array = np.frombuffer(raw, dtype=np.int16)
        float32_array = int16_array.astype(np.float32) / 32768.0
        return float32_array

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    async def transcribe(self, opus_frames: List[bytes]) -> str:
        """
        异步转写：接收 Opus 帧列表，返回识别文字（空列表或静音返回空字符串）。

        该方法在线程池中运行 CPU 密集型推理，不阻塞事件循环。
        """
        if not opus_frames:
            return ""

        # 在线程池中执行（避免阻塞 asyncio event loop）
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._sync_transcribe, opus_frames)
        return result

    def _sync_transcribe(self, opus_frames: List[bytes]) -> str:
        """同步转写方法，在线程池中调用。"""
        try:
            self._load_model()
            audio = self._opus_frames_to_float32(opus_frames)

            if len(audio) < 1600:  # 少于 0.1 秒，直接返回空
                logger.debug("音频过短，跳过转写")
                return ""

            vad_params = {}
            if self._vad_filter:
                vad_params = {
                    "vad_filter": True,
                    "vad_parameters": {
                        "min_silence_duration_ms": self._vad_min_silence_ms
                    },
                }

            segments, info = self._model.transcribe(
                audio,
                language=self._language if self._language else None,
                beam_size=self._beam_size,
                **vad_params,
            )

            text_parts = [seg.text.strip() for seg in segments]
            result = "".join(text_parts).strip()
            logger.info(f"STT 转写结果：'{result}'（语言={info.language}）")
            return result

        except Exception as e:
            logger.error(f"STT 转写失败：{e}", exc_info=True)
            return ""

    def preload(self):
        """预加载模型（可在启动时调用，避免第一次对话时的延迟）。"""
        self._load_model()
