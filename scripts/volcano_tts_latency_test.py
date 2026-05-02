"""火山豆包 TTS HTTP-SSE 首块延迟实测脚本（PC 上跑，不依赖项目 venv 之外的东西）。

用法：python3 scripts/volcano_tts_latency_test.py
"""
import base64
import json
import time

import requests

API_KEY = "2262c27d-d9c5-442a-aac6-d820c6b5eb4e"
RESOURCE_ID = "seed-tts-2.0"
SPEAKER = "zh_female_vv_uranus_bigtts"
TEXT = "你好呀你好呀，这是一段火山豆包语音合成首块延迟测试文本。"

URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"

headers = {
    "X-Api-Key": API_KEY,
    "X-Api-Resource-Id": RESOURCE_ID,
    "Content-Type": "application/json",
    "Connection": "keep-alive",
}

payload = {
    "user": {"uid": "latency_test"},
    "req_params": {
        "text": TEXT,
        "speaker": SPEAKER,
        "audio_params": {
            "format": "mp3",
            "sample_rate": 24000,
        },
    },
}


def parse_sse(stream):
    event = {"event": "message", "data": ""}
    for raw in stream:
        line = raw.decode("utf-8").strip()
        if line == "":
            if event["data"]:
                event["data"] = event["data"].rstrip("\n")
                yield event
            event = {"event": "message", "data": ""}
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            field, value = line.split(":", 1)
            value = value.lstrip()
            if field == "data":
                event["data"] += value + "\n"
            elif field == "event":
                event["event"] = value
    if event["data"]:
        event["data"] = event["data"].rstrip("\n")
        yield event


def main():
    print(f"text 字数={len(TEXT)}  speaker={SPEAKER}  resource={RESOURCE_ID}")
    print(f"POST {URL}")
    t0 = time.perf_counter()
    resp = requests.post(URL, headers=headers, json=payload, stream=True, timeout=30)
    t_headers = time.perf_counter()
    print(f"[t={(t_headers-t0)*1000:.0f}ms] HTTP响应头到达  status={resp.status_code}  logid={resp.headers.get('X-Tt-Logid')}")
    if resp.status_code != 200:
        print("!! 非200，body:", resp.text[:500])
        return

    audio = bytearray()
    first_chunk_t = None
    chunk_count = 0
    for ev in parse_sse(resp.iter_lines()):
        try:
            data = json.loads(ev["data"])
        except Exception:
            print("无法解析:", ev["data"][:200]); continue
        code = data.get("code", 0)
        if code == 0 and data.get("data"):
            chunk = base64.b64decode(data["data"])
            if first_chunk_t is None:
                first_chunk_t = time.perf_counter()
                print(f"[t={(first_chunk_t-t0)*1000:.0f}ms] ★ 首块音频到达  size={len(chunk)}B")
            audio.extend(chunk)
            chunk_count += 1
        elif code == 20000000:
            print(f"[t={(time.perf_counter()-t0)*1000:.0f}ms] 合成完成 usage={data.get('usage')}")
            break
        elif code > 0:
            print(f"!! 错误 code={code} msg={data.get('message')}")
            break

    t_end = time.perf_counter()
    print()
    print("=" * 50)
    print(f"首块延迟        : {(first_chunk_t-t0)*1000:.0f} ms")
    print(f"全部合成完成    : {(t_end-t0)*1000:.0f} ms")
    print(f"总音频大小      : {len(audio)/1024:.1f} KB ({chunk_count} chunks)")
    print(f"参考: qwen-tts 当前 376-446ms 首块")
    print("=" * 50)

    out = "/tmp/volcano_tts_test.mp3"
    with open(out, "wb") as f:
        f.write(audio)
    print(f"音频保存: {out}（用 ffplay/mpv 试听）")


if __name__ == "__main__":
    main()
