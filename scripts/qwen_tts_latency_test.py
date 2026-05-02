"""qwen-tts 首块延迟实测（PC 上跑，与豆包脚本完全对齐用于公平对比）。

埋点定义和 src/tts/qwen_tts_client.py 一致：t0=POST前，首块=第一个非空 PCM。
"""
import base64
import json
import time

import requests

API_KEY = "sk-4529e46f796b46539ba4307d5d4fe5c2"
MODEL = "qwen3-tts-flash"
VOICE = "Cherry"
TEXT = "你好呀你好呀，这是一段火山豆包语音合成首块延迟测试文本。"  # 与豆包测试相同 28 字

URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "X-DashScope-SSE": "enable",
}

payload = {
    "model": MODEL,
    "input": {
        "text": TEXT,
        "voice": VOICE,
        "language_type": "Chinese",
    },
}


def main():
    print(f"text 字数={len(TEXT)}  voice={VOICE}  model={MODEL}")
    print(f"POST {URL}")
    t0 = time.perf_counter()
    first_chunk_t = None
    chunk_count = 0
    audio = bytearray()

    resp = requests.post(URL, headers=headers, json=payload, stream=True, timeout=60)
    t_headers = time.perf_counter()
    print(f"[t={(t_headers-t0)*1000:.0f}ms] HTTP响应头到达  status={resp.status_code}")
    if resp.status_code != 200:
        print("!! 非200，body:", resp.text[:500]); return
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        output = chunk.get("output") or {}
        audio_obj = output.get("audio") or {}
        audio_data = audio_obj.get("data")
        if audio_data:
            pcm = base64.b64decode(audio_data)
            if pcm:
                if first_chunk_t is None:
                    first_chunk_t = time.perf_counter()
                    print(f"[t={(first_chunk_t-t0)*1000:.0f}ms] ★ 首块PCM到达  size={len(pcm)}B")
                audio.extend(pcm)
                chunk_count += 1
        if output.get("finish_reason") == "stop":
            break

    t_end = time.perf_counter()
    print()
    print("=" * 50)
    print(f"首块延迟        : {(first_chunk_t-t0)*1000:.0f} ms")
    print(f"全部合成完成    : {(t_end-t0)*1000:.0f} ms")
    print(f"总PCM大小       : {len(audio)/1024:.1f} KB ({chunk_count} chunks, 24kHz s16le)")
    print("=" * 50)

    # 套 WAV 头存盘，方便试听对比
    import struct
    out = "/tmp/qwen_tts_test.wav"
    sample_rate, bits, channels = 24000, 16, 1
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(audio)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", data_size)
    with open(out, "wb") as f:
        f.write(header); f.write(audio)
    print(f"音频保存: {out}（mpv {out} 试听 Cherry 音色）")


if __name__ == "__main__":
    main()
