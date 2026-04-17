"""
edge-tts TTS 模块
使用微软 Edge TTS 服务将文本合成 Opus 音频帧，兼容现有 AudioCodec 输出链路。
"""
import asyncio
import io
from typing import List

import numpy as np

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# TTS 输出 Opus 编码参数（与 AudioCodec 输出链路一致）
TTS_SAMPLE_RATE = 16000  # Hz
TTS_CHANNELS = 1
TTS_FRAME_MS = 20  # ms per Opus frame
TTS_FRAME_SAMPLES = TTS_SAMPLE_RATE * TTS_FRAME_MS // 1000  # = 320 samples


class EdgeTTSClient:
    """
    基于 edge-tts 的语音合成客户端。

    flow:
        text → edge-tts → MP3 bytes → decode to PCM int16 (raw) →
        resample to TTS_SAMPLE_RATE → encode to Opus frames →
        List[bytes]  (可直接传入 protocol.on_incoming_audio 回调)
    """

    def __init__(self):
        config = ConfigManager.get_instance()
        self._voice = config.get_config("TTS.voice", "zh-CN-XiaoxiaoNeural")
        self._rate = config.get_config("TTS.rate", "+0%")
        self._volume = config.get_config("TTS.volume", "+0%")
        self._pitch = config.get_config("TTS.pitch", "+0Hz")
        self._opus_encoder = None  # 懒加载
        logger.info(f"EdgeTTSClient 配置：voice={self._voice}, rate={self._rate}")

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------
    def _get_opus_encoder(self):
        """获取 Opus 编码器（懒加载）。"""
        if self._opus_encoder is not None:
            return self._opus_encoder
        try:
            import opuslib
        except ImportError:
            raise RuntimeError("opuslib 未安装，请执行：pip install opuslib")
        self._opus_encoder = opuslib.Encoder(
            TTS_SAMPLE_RATE, TTS_CHANNELS, opuslib.APPLICATION_AUDIO
        )
        return self._opus_encoder

    def _mp3_bytes_to_pcm_int16(self, mp3_data: bytes, target_sr: int) -> bytes:
        """
        将 MP3 字节解码为 int16 PCM，并重采样到 target_sr Hz 单声道。
        优先使用 miniaudio，回退到 pydub + soxr。
        """
        # 方案 A：miniaudio（零外部依赖）
        try:
            import miniaudio

            decoded = miniaudio.mp3_read_s16(mp3_data)
            samples = np.frombuffer(decoded.samples, dtype=np.int16)
            src_sr = decoded.sample_rate
            src_channels = decoded.nchannels
        except ImportError:
            # 方案 B：pydub（依赖 ffmpeg）
            try:
                from pydub import AudioSegment

                seg = AudioSegment.from_mp3(io.BytesIO(mp3_data))
                seg = seg.set_channels(1).set_frame_rate(target_sr)
                return seg.raw_data  # int16 LE
            except ImportError:
                raise RuntimeError(
                    "请安装 miniaudio 或 pydub+ffmpeg：\n"
                    "  pip install miniaudio\n"
                    "  # 或\n"
                    "  pip install pydub && apt install ffmpeg"
                )

        # 转为单声道
        if src_channels > 1:
            samples = samples.reshape(-1, src_channels).mean(axis=1).astype(np.int16)

        # 重采样到 target_sr
        if src_sr != target_sr:
            try:
                import soxr

                float_in = samples.astype(np.float32) / 32768.0
                float_out = soxr.resample(float_in, src_sr, target_sr)
                samples = (float_out * 32768.0).clip(-32768, 32767).astype(np.int16)
            except ImportError:
                # 简单线性插值后备
                ratio = target_sr / src_sr
                new_len = int(len(samples) * ratio)
                indices = np.linspace(0, len(samples) - 1, new_len)
                samples = np.interp(indices, np.arange(len(samples)), samples).astype(
                    np.int16
                )

        return samples.tobytes()

    def _pcm_int16_to_opus_frames(self, pcm_bytes: bytes) -> List[bytes]:
        """将 int16 PCM 字节流切割并编码为 Opus 帧列表。"""
        encoder = self._get_opus_encoder()
        frame_byte_len = TTS_FRAME_SAMPLES * 2  # int16 = 2 bytes/sample

        # 确保长度对齐
        pad = len(pcm_bytes) % frame_byte_len
        if pad:
            pcm_bytes += b"\x00" * (frame_byte_len - pad)

        frames: List[bytes] = []
        for offset in range(0, len(pcm_bytes), frame_byte_len):
            chunk = pcm_bytes[offset : offset + frame_byte_len]
            try:
                opus_frame = encoder.encode(chunk, TTS_FRAME_SAMPLES)
                frames.append(opus_frame)
            except Exception as e:
                logger.debug(f"Opus 编码单帧失败，已跳过：{e}")
        return frames

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    async def synthesize_to_mp3(self, text: str) -> bytes:
        """调用 edge-tts 将文本合成为 MP3 字节。"""
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError("edge-tts 未安装，请执行：pip install edge-tts")

        communicate = edge_tts.Communicate(
            text,
            voice=self._voice,
            rate=self._rate,
            volume=self._volume,
            pitch=self._pitch,
        )

        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

        mp3_data = buf.getvalue()
        if not mp3_data:
            logger.warning(f"edge-tts 未生成任何音频，text='{text[:30]}'")
        return mp3_data

    async def synthesize_to_opus_frames(self, text: str) -> List[bytes]:
        """
        主接口：文本 → Opus 帧列表（可直接送入 on_incoming_audio）。
        """
        if not text or not text.strip():
            return []

        try:
            mp3_data = await self.synthesize_to_mp3(text)
            if not mp3_data:
                return []

            loop = asyncio.get_event_loop()
            pcm_bytes = await loop.run_in_executor(
                None, self._mp3_bytes_to_pcm_int16, mp3_data, TTS_SAMPLE_RATE
            )
            frames = self._pcm_int16_to_opus_frames(pcm_bytes)
            logger.info(
                f"TTS 合成完成：text_len={len(text)}, opus_frames={len(frames)}"
            )
            return frames
        except Exception as e:
            logger.error(f"TTS 合成失败：{e}", exc_info=True)
            return []
