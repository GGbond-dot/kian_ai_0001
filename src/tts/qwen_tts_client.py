"""
Qwen TTS 模块
使用通义千问 Qwen-TTS（DashScope）将文本合成为 Opus 音频帧。
"""
import asyncio
import base64
import json
import os
from typing import List

import httpx
import numpy as np

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

TTS_SAMPLE_RATE = 16000
TTS_CHANNELS = 1
TTS_FRAME_MS = 20
TTS_FRAME_SAMPLES = TTS_SAMPLE_RATE * TTS_FRAME_MS // 1000


class QwenTTSClient:
    """
    基于 DashScope Qwen-TTS 的在线语音合成客户端。

    调用 DashScope SSE 流式接口获取 24kHz 单声道 PCM 数据，再重采样并编码为
    16kHz Opus 帧，兼容现有 AudioCodec 输出链路。
    """

    def __init__(self):
        config = ConfigManager.get_instance()
        self._base_url = config.get_config(
            "TTS.dashscope_base_url", "https://dashscope.aliyuncs.com/api/v1"
        ).rstrip("/")
        self._model = config.get_config("TTS.qwen_model", "qwen3-tts-flash")
        self._voice = config.get_config("TTS.qwen_voice", "Cherry")
        self._language_type = config.get_config(
            "TTS.qwen_language_type", "Chinese"
        )
        self._instructions = config.get_config("TTS.qwen_instructions", "")
        self._optimize_instructions = bool(
            config.get_config("TTS.qwen_optimize_instructions", False)
        )
        self._api_key = (
            config.get_config("TTS.dashscope_api_key", "")
            or config.get_config("CAMERA.VLapi_key", "")
            or (
                config.get_config("LLM.api_key", "")
                if "dashscope.aliyuncs.com"
                in str(config.get_config("LLM.base_url", "")).lower()
                else ""
            )
            or os.getenv("DASHSCOPE_API_KEY", "")
        )
        self._opus_encoder = None
        logger.info(
            "QwenTTSClient 配置：model=%s, voice=%s, base_url=%s",
            self._model,
            self._voice,
            self._base_url,
        )

    def _get_opus_encoder(self):
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

    def _pcm_s16_bytes_to_target_pcm(self, pcm_bytes: bytes, source_sr: int) -> bytes:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        if samples.size == 0:
            return b""

        if source_sr != TTS_SAMPLE_RATE:
            try:
                import soxr

                float_in = samples.astype(np.float32) / 32768.0
                float_out = soxr.resample(float_in, source_sr, TTS_SAMPLE_RATE)
                samples = (float_out * 32768.0).clip(-32768, 32767).astype(np.int16)
            except ImportError:
                ratio = TTS_SAMPLE_RATE / source_sr
                new_len = int(len(samples) * ratio)
                indices = np.linspace(0, len(samples) - 1, new_len)
                samples = np.interp(indices, np.arange(len(samples)), samples).astype(
                    np.int16
                )

        return samples.tobytes()

    def _pcm_int16_to_opus_frames(self, pcm_bytes: bytes) -> List[bytes]:
        encoder = self._get_opus_encoder()
        frame_byte_len = TTS_FRAME_SAMPLES * 2

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
                logger.debug("QwenTTS：Opus 编码单帧失败，已跳过：%s", e)
        return frames

    async def _stream_pcm_from_qwen(self, text: str) -> bytes:
        if not self._api_key:
            raise RuntimeError(
                "未配置 DashScope API Key，请设置 TTS.dashscope_api_key 或 DASHSCOPE_API_KEY"
            )

        url = f"{self._base_url}/services/aigc/multimodal-generation/generation"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "enable",
        }
        payload = {
            "model": self._model,
            "input": {
                "text": text,
                "voice": self._voice,
                "language_type": self._language_type,
            },
        }
        if self._instructions:
            payload["input"]["instructions"] = self._instructions
            payload["input"]["optimize_instructions"] = self._optimize_instructions

        pcm_chunks: List[bytes] = []
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("QwenTTS：忽略无法解析的 SSE 片段：%s", data[:120])
                        continue

                    output = chunk.get("output") or {}
                    audio = output.get("audio") or {}
                    audio_data = audio.get("data")
                    if audio_data:
                        pcm_chunks.append(base64.b64decode(audio_data))

                    if output.get("finish_reason") == "stop":
                        break

        return b"".join(pcm_chunks)

    async def synthesize_to_opus_frames(self, text: str) -> List[bytes]:
        if not text or not text.strip():
            return []

        try:
            pcm_24k = await self._stream_pcm_from_qwen(text)
            if not pcm_24k:
                logger.warning("QwenTTS：未生成任何音频，text='%s'", text[:30])
                return []

            loop = asyncio.get_event_loop()
            pcm_16k = await loop.run_in_executor(
                None, self._pcm_s16_bytes_to_target_pcm, pcm_24k, 24000
            )
            frames = self._pcm_int16_to_opus_frames(pcm_16k)
            logger.info(
                "Qwen TTS 合成完成：text_len=%s, opus_frames=%s",
                len(text),
                len(frames),
            )
            return frames
        except Exception as e:
            logger.error("Qwen TTS 合成失败：%s", e, exc_info=True)
            return []
