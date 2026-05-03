# 物流系统终端机器人 — 平板直连 TTS（接力交接 v4，**改造已完成**）

> 接 `background02.md`（v3 已落地：edge-tts→qwen-tts，端到端 1554ms）。
> v4 平板直连云 TTS 改造 + LLM/平板双预热已落地实测，本文档已更新为完成态。

---

## ★ 开发模式（每次开新对话先看这条）

- **PC（Ubuntu 24.04）只写代码、不跑项目**
- 通过 **syncpi** 同步到开发板（Ubuntu 22.04，ARM）运行
- 所有性能数据均来自开发板/平板实测
- 平板（华为 MatePad）= 展示 + 麦克风 + 音频播放端，APK 是 WebView 壳子加载 `http://192.168.10.1:8080/`
- **平板代码就在本仓库** `android_webview/`（Kotlin+WebView），UI 实质是 `src/display/web_static/` 下的 HTML/JS
- PC 上允许做的：纯静态校验（`python3 -c "import ast"`、`node --check`、`json.load`），不会启动项目

---

## v4 阶段：本次已完成的事

### 1. 豆包 TTS（火山引擎）评估 → **不切**（保留 v3 否决结论）

测试脚本：`scripts/qwen_tts_latency_test.py`、`scripts/volcano_tts_latency_test.py`

火山新版鉴权确认（与文档不同）：**只需 `X-Api-Key` + `X-Api-Resource-Id`**，不要 App-Id / Access-Key。
- Endpoint: `https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse`
- Resource-Id: `seed-tts-2.0`
- 音色 ID 示例: `zh_female_vv_uranus_bigtts`

**决策**：不切。延迟持平、音色持平、带宽优势在当前管线消化。

### 2. 平板直连云 TTS PoC → 数据漂亮（保留作回归）

PoC 文件（**保留勿删**）：
- `src/display/web_static/tts_poc.html` / `tts_poc.js`
- `src/display/web_server.py` 的 `@app.get("/tts_poc")` 路由
- 平板访问 `http://192.168.10.1:8080/tts_poc`

**核心发现**：浏览器原生 HTTP/2 长连接复用，省 TLS 握手 ~250ms；稳态首块 ~283ms，冷启动 533ms。

### 3. 正式改造已落地

#### 3.1 改动文件清单（已实施）

| 文件 | 改动 |
|---|---|
| `config/config.json` | 加 `TTS.tablet_direct: true`、`TTS.tablet_api_key`、`TTS.tablet_fallback_cooldown_count: 3` |
| `src/protocols/local_agent_protocol.py` | `_tts_sink_one_segment` 按 `tablet_direct` 分发；新增 `set_tts_remote_text_sink` / `on_tablet_audio_out_text` / 段 id 计数 / 冷却 / fallback 重合成；新增 `_kick_llm_fast_warmup` |
| `src/display/web_server.py` | `/ws/audio_out` 接收文本帧；新增 `broadcast_audio_out_text` 下行、`set_audio_out_text_callback` 上行；`/` 路由加 no-cache 头 |
| `src/display/web_display.py` | 透传 `broadcast_audio_out_text` / `set_audio_out_text_callback` |
| `src/plugins/ui.py` | 把 text_sink 注册给 protocol，把上行回调挂到 `protocol.on_tablet_audio_out_text` |
| `src/display/web_static/tts_direct.js`（**新建**） | 监听 audio_out WS 的 `tts_text` JSON → fetch dashscope SSE → Web Audio 流式排队播放；首块 >3s 或 fetch 失败发 `tts_failed` 上报；段首 15ms fade-in；起步 warmup |
| `src/display/web_static/index.html` | 引入 `tts_direct.js?v=4`，所有脚本加 `?v=N` 缓存破除 |
| `src/display/web_static/audio_out.js` | **未动**，作为 mp3 fallback 路径保留 |

#### 3.2 协议（已落地）

**开发板 → 平板**（在 `/ws/audio_out` 上发 JSON 文本帧，与 mp3 二进制帧共享同一通道）：
```json
{"type":"tts_text", "segment_id":7, "text":"你好呀，欢迎使用", "voice":"Cherry"}
```

**平板 → 开发板**（在同通道发 JSON 文本帧）：
```json
{"type":"tts_failed", "segment_id":7, "reason":"fetch_error|http_5xx|timeout", "text":"你好呀，欢迎使用"}
```

#### 3.3 fallback 机制（已实施）

- 平板上报 `tts_failed` → 开发板用 `text` 字段直接走旧 mp3 路径合成 → `broadcast_audio_out` 二进制 → `audio_out.js` 播
- 同段不重试两次（`_tablet_failed_handled` set）
- 连续 3 段失败 → 本轮对话剩余段降级走 mp3 旧路径；下轮 `_reset_tablet_session_state` 自动恢复尝试

### 4. 双预热（已落地，效果验证过）

#### 4.1 后端 LLM 预热

- `_kick_llm_fast_warmup()` 在 `listen.start` 时跑一发 1 token 的 qwen-flash chat completion，把 TLS+连接池热好
- 4s 内合并防重复
- 实测：第一次冷启动握手 1056ms，后续连接池保持 150-340ms

#### 4.2 平板侧 fetch 预热

- `tts_direct.js` 的 `warmupFetch()` 在 WS onopen 时空跑一发 dashscope SSE，读到首块就 abort
- 浏览器维护 HTTP/2 长连接，后续真合成走同一通道
- `warmupDone` 守卫，每个 WS 连接生命周期内只热一次

### 5. 段首爆破音修复

- `tts_direct.js.playPcmChunk(pcmU8, fadeIn)`，每段第一个 chunk 加 15ms 线性渐入（`SAMPLE_RATE * 0.015 = 360` 样本）
- 与 v3 后端 qwen_tts_client 的 fade-in 等价，但放在了平板侧

---

## 当前性能基线（2026-05-02 实测）

**端到端首声 = 松开按钮 → 平板听到第一声**

### v4 + 云端 STT（qwen3-asr-flash）—— 当前线上配置

回滚 paraformer 流式后的稳态实测（2026-05-02 15:10）：

| 轮次 | 音频时长 | STT | LLM Tier1 总 | TTS 推送 | 后端总 | + 平板 fetch | **端到端首声** |
|---|---|---|---|---|---|---|---|
| 1 | 1.85s | 688ms | 490ms | 0ms | 1178ms | ~283ms | **~1.46s** |
| 2 | 2.08s | 555ms | 403ms | 0ms | 958ms | ~283ms | **~1.24s** |
| 3 | 1.98s | 464ms | 478ms | 0ms | 942ms | ~283ms | **~1.23s** |

**稳态 ~1.23-1.24s**，连接池热起来后比首次切换（1.36-1.51s）再快 100-200ms。
准确率正常（"你叫什么名字呀？"/"你有哪些功能呢？"），简繁混淆问题彻底消失。

### 早期实测（仅供参考）

| 阶段 | STT | Tier1 | 端到端首声 | 备注 |
|---|---|---|---|---|
| qwen-asr 刚切换（14:27） | 573-720ms | 499-557ms | 1.36-1.51s | 连接池冷 |
| paraformer 流式（15:05） | 1274-1518ms | 351-404ms | ~2.0s | **慢于批量**，已回滚 |

**对比 v4 whisper base 基线（旧）**：稳态 1.23-1.24s vs 1.60s，**降 ~360ms**。
**更大的收益在准确率**：实测全对（"你叫什么名字呀？" / "你有哪些功能呢？" / "你好呀"），whisper base 之前的简繁混淆（"你有什麼功能" / "尼海亞尼亞"）彻底消失。

**当前耗时占比**（稳态平均）：
- STT（qwen-asr POST→响应）：~46% ← **依然最大头**
- LLM Tier1（POST + 流式拿够字）：~40%
- TTS push：0%
- 平板 fetch dashscope 首块：~22%

### v4 whisper base（旧基线，仅留作对比）

| 轮次 | STT | LLM Tier1 | 后端总 | 端到端首声 |
|---|---|---|---|---|
| 1（冷） | 798ms | 829ms | 1632ms | ~1.91s |
| 2（稳态） | 656ms | 662ms | 1319ms | ~1.60s |

---

## 下一步要做的事（按性价比排序）

### P0 — 已完成：云端 STT 切换到 qwen3-asr-flash

**结论**：保留。准确率明显更好（简繁混淆消失），延迟从 656ms→573-684ms 略降，端到端从 1.60s→1.36-1.51s。
**踩坑记录**：
- `QwenASRSTT` 一开始没实现 `transcribe_pcm`，平板麦克风走 PCM 路径直接被静默跳过（`WARNING - 当前 STT provider 不支持 transcribe_pcm`），已补上 `_sync_transcribe_pcm` 复用 `_transcribe_wav_bytes`
- segment_id 在开发板进程级累加，平板刷页面后 `nextPlayId=0` 永远等不到 N 号段死锁；改 `tts_direct.js` 让前端跟随后端的第一个段 id 作为起点（`?v=5`）

### P0' — 流式 STT（paraformer-realtime） — **已实施，已回滚**

**实测结果**（2026-05-02）：
| 模型 | STT 平均耗时 | vs 批量 |
|---|---|---|
| qwen3-asr-flash 批量 | ~660ms | 基线 |
| paraformer-realtime-v2 流式 | **~1361ms** | **慢 700ms** |

**为什么流式反而更慢**：paraformer-realtime 的"实时"是指识别过程实时，但**finalize 有 ~1s 内置延迟**——服务端收到 finish-task 后还要跑标点预测/句末检测/反向文本规整等后处理。短句（<3s）这部分尾延迟比批量的整段处理还慢。

**流式只在长句（>5s）才有优势**。本项目语音对话基本都是短指令，不适用。

**当前状态**：`STT.streaming_enabled: false`，跑批量 qwen3-asr-flash。流式代码保留作未来长句场景的可选项。

**实现保留**：
- `src/stt/qwen_stream_stt.py` — paraformer-realtime-v2 WebSocket duplex 客户端
- `local_agent_protocol.py` — `_kick_stream_stt_start` / 缓冲始终保留作 fallback / catchup cursor 防丢帧
- 流式失败/超时自动降级批量

**给后续的人**：不要为短指令场景重新打开 streaming_enabled；除非业务场景变成长句口述，才值得评估。

### P1 — Tier1 启动延迟微调

LLM 响应到推 TTS 之间有 144-184ms（`ROUTER.fallback_scan_chars: 30` 的扫描期）。降到 15 大概省 80-100ms，代价是 fallback 短语只在前 15 字内有效。**先不动，等 STT 切完再看是否还有必要**。

### P2 — Tier 2 直达关键词词表

`config.ROUTER.tier2_keywords` 当前空 list；如果实测发现 Tier 1 误判走了 Tier 2 fallback 频繁，可以加几个关键词直达。

### P3 — 常态化 ROS Bridge

`subprocess.Popen` → 主进程驻留 publisher 单例（mapping.view 走 ROS 时每次起进程的开销）。

### P4 — 段间接缝感

已用 fade-in 解决主要爆音（v3 后端 + v4 平板侧），如果用户后续仍觉接缝明显再做。

### P5 — 打断机制

不在本期。后续：开发板下发 `{type:"tts_cancel"}` → 平板 abort fetch + 停 AudioContext + 清队列。

---

## 已敲定的决策（v3+v4 总集）

| # | 决策 | 状态 |
|---|---|---|
| 1 | API key 写 APK / JS 里（内网信任） | 已落地，dashscope key 写在 `tts_direct.js` |
| 2 | JS 路径（WebView 里 fetch + Web Audio） | 已落地 |
| 3 | 段级流式保留，打断后做 | 已落地，打断未做 |
| 4 | 平板合成失败 → 反馈开发板 → 旧路径兜底 | 已落地 |
| 5 | 先 PoC 后改造 | 已完成 |
| 6 | qwen-flash 在 listen.start 时主动预热 | 已落地（v4 新增） |
| 7 | 平板侧 fetch 在 WS onopen 时主动预热 | 已落地（v4 新增） |
| 8 | 段首 15ms fade-in 在平板侧做，不在后端 | 已落地（v4 新增） |
| 9 | 静态资源加 `?v=N`、HTML 加 no-cache 头 | 已落地（v4 新增），防 WebView 缓存旧版 |

---

## 协议字段（已落地）

**开发板 → 平板**（`/ws/audio_out` 文本帧）：
```json
{"type":"tts_text", "segment_id":<int>, "text":"...", "voice":"Cherry"}
```

**平板 → 开发板**（`/ws/audio_out` 文本帧）：
```json
{"type":"tts_failed", "segment_id":<int>, "reason":"fetch_error|http_4xx|http_5xx|timeout", "text":"..."}
```

**fallback 触发条件**（平板侧任一）：
- `fetch` 抛 TypeError（CORS / 断网）→ `fetch_error`
- HTTP ≥ 500 → `http_5xx`；其他非 200 → `http_<status>`
- 首块超时 > 3000ms → `timeout`

---

## 安全 / 准备工作（v4 改造前已写，仍待做）

1. **轮换 API key**：当前 dashscope key（`sk-4529e46f796b46539ba4307d5d4fe5c2`）已在多个测试脚本里硬编码且对话曝光过，正式部署前去 dashscope 控制台换新 key
2. **决定平板 key 是否和开发板分开**：当前 `TTS.tablet_api_key` 是独立字段（已预留），但值与后端相同。建议正式部署时分开，方便单独限额 / 审计

---

## v4 之前已做（继承自 background01-02，保留有效）

- 三层路由架构（Tier 0 关键词直达 / Tier 1 qwen-flash / Tier 2 qwen3-coder-next）
- qwen-tts 流式管线 + 段起始 15ms fade-in 抑制爆音 + 纯标点段过滤
- 6 个激活的 MCP 工具（drone.takeoff/land/status, mapping.view, audio_speaker volume）

---

## 协作约定（沿用）

- PC 上不跑项目代码，syncpi 到开发板再测；PC 允许做静态语法/JSON 校验
- 改动前先讨论方案，避免大刀阔斧改完才发现方向不对
- 改完代码后等用户实测日志反馈，再决定下一步
- **平板 UI 改动改 `src/display/web_static/` 下的 JS 即可，APK 通常不动**
- 改 JS 后注意更新 `index.html` 里 `?v=N` 数字，避免 WebView 缓存旧版

---

## 项目内重点文档

- `background01.md` — v2（三层路由架构落地）
- `background02.md` — v3（edge-tts→qwen-tts + 段爆音修复）
- **`background03.md` — 本文档（v4 平板直连改造已落地 + 双预热）**
- `background04.md` — v5（场景 B：DroneCommandBridge 常驻 + drone.hover 新增 + mapping.view 改纯文本）
- `FIRST_RESPONSE_LATENCY.md` — 早期方案分析
- `SLAM_WEB_VIEWER_DESIGN.md` — SLAM 模块设计

---

## 关键文件位置速查

```
项目根: /home/kian/kian_project/aiagent

后端 TTS / 协议:
  src/tts/qwen_tts_client.py              ← qwen-tts 流式客户端（fallback mp3 路径用）
  src/protocols/local_agent_protocol.py    ← _tts_sink_one_segment / on_tablet_audio_out_text / _kick_llm_fast_warmup
  src/display/web_server.py                ← /ws/audio_out 双向 + broadcast_audio_out_text
  src/display/web_display.py               ← WebDisplay 透传层

平板 UI（开发板托管，平板 WebView 加载）:
  src/display/web_static/index.html        ← 主页面（脚本带 ?v=N）
  src/display/web_static/audio_out.js      ← 旧 mp3 fallback 路径
  src/display/web_static/tts_direct.js     ← v4 新增：tts_text → 直连 dashscope
  src/display/web_static/tts_poc.html / tts_poc.js  ← 回归测试 PoC

平板 APK（v4 未动，验证证明无需改）:
  android_webview/app/src/main/java/com/kian/aiagent/MainActivity.kt
  android_webview/app/src/main/java/com/kian/aiagent/AudioBridge.kt

STT（下一步重点）:
  src/stt/whisper_stt.py                   ← 当前默认（base / CPU / int8）
  src/stt/qwen_asr_stt.py                  ← 已实现的 qwen-asr 客户端，未启用

配置:
  config/config.json
    TTS.provider: "qwen"
    TTS.tablet_direct: true                ← 主开关，false 即回退旧路径
    TTS.tablet_api_key: "..."
    STT.provider: "whisper"                ← 改成 "qwen" 即切云端 STT（下一步要做）
```

---

## 给下一个 Claude 的话

接力建议：

1. **当前状态**：v4 平板直连 TTS + 双预热 + 段首 fade-in + qwen3-asr-flash 云 STT **全部已落地实测**，端到端首声稳态 ~1.23-1.24s（连接池热起来后），准确率明显改善
2. **paraformer 流式 STT 已实测、已否决** —— 短句场景下比批量慢 700ms（finalize 有 ~1s 内置延迟）。代码保留，开关默认关闭，不要无视实测数据重新打开。
3. 不要重测豆包 TTS（v4 已否决）
4. 不要碰 `tts_poc.html` / `tts_poc.js`（保留作回归对比）
5. PC 上允许静态语法校验（`python3 -c "import ast"`、`node --check`、`json.load`），不要尝试启动项目；运行实测让用户在开发板上做
6. 改 web_static 下的 JS 后记得更新 `index.html` 里的 `?v=N`，否则 WebView 会跑旧版（已踩过坑）
7. 当前 dashscope API key 已多次曝光，正式部署前用户需要轮换；这件事要在适当时候提醒
8. **段 id 协议契约**：开发板 `_tablet_segment_id` 进程级累加，从不归零；平板 `tts_direct.js` 跟随第一个收到的 id 作为播放起点。改任一侧前先理解这个契约，否则会回到死锁。
9. **STT.transcribe_pcm 是必需接口**：平板麦克风走 PCM 不走 opus，新增 STT provider 时务必实现 `transcribe_pcm(pcm_bytes)`，否则会被 protocol 静默跳过
