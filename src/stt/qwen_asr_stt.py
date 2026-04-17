"""
Qwen ASR STT 模块
使用通义千问 Qwen-ASR（DashScope OpenAI 兼容接口）进行语音识别。
"""
import asyncio
import base64
import io
import os
import wave
from typing import List

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class QwenASRSTT:
    """
    基于 Qwen-ASR 的在线语音识别。

    接收 Opus 编码的音频帧列表，解码为 16kHz 单声道 WAV 后，调用
    DashScope OpenAI 兼容接口进行识别。
    """

    def __init__(self):
        config = ConfigManager.get_instance()
        self._language = config.get_config("STT.language", "zh")
        self._enable_itn = bool(config.get_config("STT.enable_itn", False))
        self._base_url = config.get_config(
            "STT.dashscope_base_url",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self._model = config.get_config("STT.qwen_model", "qwen3-asr-flash")
        self._api_key = (
            config.get_config("STT.dashscope_api_key", "")
            or config.get_config("CAMERA.VLapi_key", "")
            or (
                config.get_config("LLM.api_key", "")
                if "dashscope.aliyuncs.com"
                in str(config.get_config("LLM.base_url", "")).lower()
                else ""
            )
            or os.getenv("DASHSCOPE_API_KEY", "")
        )
        self._client = None
        self._opus_decoder = None
        logger.info(
            "QwenASRSTT 配置：model=%s, base_url=%s, language=%s",
            self._model,
            self._base_url,
            self._language,
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai 未安装，请执行：pip install openai")
        if not self._api_key:
            raise RuntimeError(
                "未配置 DashScope API Key，请设置 STT.dashscope_api_key 或 DASHSCOPE_API_KEY"
            )
        self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    def _get_opus_decoder(self):
        if self._opus_decoder is not None:
            return self._opus_decoder
        try:
            import opuslib
        except ImportError:
            raise RuntimeError("opuslib 未安装，请执行：pip install opuslib")
        self._opus_decoder = opuslib.Decoder(16000, 1)
        return self._opus_decoder

    def _opus_frames_to_wav_bytes(self, opus_frames: List[bytes]) -> tuple[bytes, int]:
        decoder = self._get_opus_decoder()
        pcm_chunks: List[bytes] = []
        frame_size = 320  # 20ms @ 16kHz

        for frame in opus_frames:
            if not frame:
                continue
            try:
                pcm = decoder.decode(frame, frame_size)
                pcm_chunks.append(pcm)
            except Exception as e:
                logger.debug("QwenASRSTT：Opus 解码单帧失败，已跳过：%s", e)

        if not pcm_chunks:
            return b"", 0

        pcm_bytes = b"".join(pcm_chunks)
        with io.BytesIO() as buf:
            with wave.open(buf, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(pcm_bytes)
            return buf.getvalue(), len(pcm_bytes) // 2

    def _wav_bytes_to_data_uri(self, wav_bytes: bytes) -> str:
        encoded = base64.b64encode(wav_bytes).decode("ascii")
        return f"data:audio/wav;base64,{encoded}"

    async def transcribe(self, opus_frames: List[bytes]) -> str:
        if not opus_frames:
            return ""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_transcribe, opus_frames)

    def _sync_transcribe(self, opus_frames: List[bytes]) -> str:
        try:
            wav_bytes, sample_count = self._opus_frames_to_wav_bytes(opus_frames)
            if not wav_bytes:
                return ""

            if sample_count < 1600:
                logger.debug("QwenASRSTT：音频过短，跳过转写")
                return ""

            client = self._get_client()
            request_kwargs = {
                "model": self._model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": self._wav_bytes_to_data_uri(wav_bytes)
                                },
                            }
                        ],
                    }
                ],
            }

            extra_body = {"asr_options": {"enable_itn": self._enable_itn}}
            if self._language:
                extra_body["asr_options"]["language"] = self._language
            request_kwargs["extra_body"] = extra_body

            completion = client.chat.completions.create(**request_kwargs)
            result = ""
            if completion.choices:
                message = completion.choices[0].message
                result = (getattr(message, "content", "") or "").strip()

            logger.info("Qwen ASR 转写结果：'%s'", result)
            return result
        except Exception as e:
            logger.error("Qwen ASR 转写失败：%s", e, exc_info=True)
            return ""

    def preload(self):
        """接口兼容：在线 STT 无需模型预加载。"""
        return None
