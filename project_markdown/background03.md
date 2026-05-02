# 物流系统终端机器人 — 平板直连 TTS（接力交接 v4）

> 接 `background02.md`（v3 已落地：edge-tts→qwen-tts，端到端 1554ms）。  
> 本文档为 v4 阶段——**评估并设计"平板端直连云 TTS"方案**，PoC 完成、改造未做。

---

## ★ 开发模式（每次开新对话先看这条）

- **PC（Ubuntu 24.04）只写代码、不跑项目**
- 通过 **syncpi** 同步到开发板（Ubuntu 22.04，ARM）运行
- 所有性能数据均来自开发板/平板实测
- 平板（华为 MatePad）= 展示 + 麦克风 + 音频播放端，APK 是 WebView 壳子加载 `http://192.168.10.1:8080/`
- **平板代码就在本仓库** `android_webview/`（Kotlin+WebView），UI 实质是 `src/display/web_static/` 下的 HTML/JS

---

## v4 阶段：本次已完成的事

### 1. 豆包 TTS（火山引擎）评估 → **不切**

测试脚本：`scripts/qwen_tts_latency_test.py`、`scripts/volcano_tts_latency_test.py`

火山新版鉴权确认（与文档不同）：**只需 `X-Api-Key` + `X-Api-Resource-Id`**，不要 App-Id / Access-Key。  
- Endpoint: `https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse`
- Resource-Id: `seed-tts-2.0`（语音合成大模型 2.0）
- 音色 ID 示例: `zh_female_vv_uranus_bigtts`

**开发板实测对比（28 字测试文本，多次均值）**：

| 指标 | qwen-tts | 豆包 TTS | 备注 |
|---|---|---|---|
| 首块延迟 | ~478 ms | ~460 ms | 噪声内 |
| 全部合成 | ~1440 ms | ~1400 ms | 持平 |
| 数据量 | ~270 KB (PCM) | ~45 KB (mp3) | 豆包 1/6 |
| 音色（用户主观） | Cherry | uranus_bigtts | 差不多 |

**决策**：不切。延迟持平、音色持平、带宽优势在当前管线（qwen 转 mp3 后再推平板）被消化。改半天代码收益≈0。

### 2. 平板直连云 TTS PoC → **数据漂亮，确定要做**

PoC 文件（**保留勿删，作回归测试用**）：
- `src/display/web_static/tts_poc.html`
- `src/display/web_static/tts_poc.js`
- `src/display/web_server.py` 加了 `@app.get("/tts_poc")` 路由

平板访问：`http://192.168.10.1:8080/tts_poc`

**平板浏览器实测（同 28 字文本，连续点击 6 次）**：

| 次数 | 首块 | 备注 |
|---|---|---|
| 1 | 533 ms | 冷启动 TLS 全握手 |
| 2 | 484 ms | 升温中 |
| 3 | **216 ms** | 连接复用生效 |
| 4 | 291 ms | |
| 5 | 325 ms | |
| 6 | 299 ms | |

**稳态首块 ~283ms**（后 4 次均值）。

**核心发现**：浏览器原生保持 HTTPS 长连接 + HTTP/2 多路复用，省掉每次 TLS 握手 ~200ms。开发板用 httpx 每次新建连接（ffmpeg 子进程模型让复用困难），这是平板直连的根本优势。

**端到端对比**：

| 路径 | 首块 | + 其他 | 端到端首声 |
|---|---|---|---|
| 当前（开发板→mp3→平板） | 478ms | ffmpeg 50-100 + WS 推送 10-50 + 平板解码 50 | **590-680 ms** |
| 平板直连（稳态） | 283ms | Web Audio 启动 60 | **~343 ms** |
| 平板直连（冷启动最坏） | 533ms | +60 | ~593 ms |

**预期节省 250-340 ms（稳态），最坏情况持平**。

### 3. 顺带确认的事实

- ✅ **CORS 通过**：dashscope 对浏览器开放，无需代理
- ✅ **Web Audio API 流式播 PCM 工作正常**（PoC 里逐 chunk 创建 BufferSource 排队播放，能听到声）
- ✅ **HTTP 响应头与首块只差 2-13ms**：服务端边算边返回
- ✅ **新版火山鉴权方式确认**（见上面）

---

## v4 已敲定的 5 个决策

| # | 决策 | 原因 |
|---|---|---|
| 1 | API key 写 APK / JS 里（内网信任） | 平板单台、内网部署、APK 不公网下发 |
| 2 | JS 路径（在 WebView 里 fetch + Web Audio） | 不用 Kotlin。PoC 验证 JS 已够快，CORS 没拦 |
| 3 | 段级流式保留，打断机制后做 | 段级已是当前架构核心；打断不是核心需求 |
| 4 | 平板合成失败 → 反馈开发板 → 旧路径兜底（双路径并存） | 离线/限流时不能哑掉 |
| 5 | 先 PoC 后改造 | 已完成，数据支持改造 |

---

## v4 未完成：正式改造方案（下次直接按这个动）

### 改造文件清单

| 文件 | 改动 | 工作量 |
|---|---|---|
| `src/protocols/local_agent_protocol.py` | `_tts_sink_one_segment` 按 `TTS.tablet_direct` 分发：true 推 `{type:"tts_text"}`，false 走旧 mp3 路径 | 中 |
| `src/display/web_static/tts_direct.js`（新建） | 复用 `tts_poc.js` 核心逻辑：监听 WS `tts_text` → fetch dashscope → Web Audio 流式播放 | 中 |
| `src/display/web_static/audio_out.js` | 保留旧 mp3 播放路径（fallback 用），不动逻辑 | 0 |
| `src/display/web_static/index.html` | 引入 `tts_direct.js` | 小 |
| `src/display/web_server.py` | WS 通道增加新消息类型 `tts_text` 的下行 + `tts_failed` 上行 | 中 |
| `config/config.json` | `TTS.tablet_direct: true` 开关；`TTS.tablet_api_key` 单独存（避免和后端 key 混淆，将来可不同） | 小 |

**总工作量：约半天。**

### 协议字段（草案）

**开发板 → 平板**（替代当前的 mp3 二进制帧）：

```json
{
  "type": "tts_text",
  "segment_id": 7,           // 段序号，平板按序号排队播放
  "text": "你好呀，欢迎使用",
  "voice": "Cherry",          // 开发板下发，将来换音色不用改 APK
  "is_final": false           // true 表示这一轮 LLM 回复的最后一段
}
```

**平板 → 开发板**（合成失败时反馈，触发 fallback）：

```json
{
  "type": "tts_failed",
  "segment_id": 7,
  "reason": "fetch_error|http_5xx|cors|timeout",
  "text": "你好呀，欢迎使用"   // 回传文本，开发板拿去走旧路径补合成
}
```

### Fallback 机制（决策 4）

**触发条件**（平板侧任一）：
- `fetch` 抛 TypeError（CORS / 断网）
- HTTP 状态码 ≥ 500
- 首块超时 > 3s（保护性 timeout）

**触发动作**：
1. 平板立即发 `tts_failed` 给开发板
2. 开发板收到后，对该 `segment_id` 走旧路径：调 qwen-tts → ffmpeg → 推 mp3 给平板
3. 平板 `audio_out.js`（旧逻辑保留）正常播放该 mp3
4. **同段不重试两次**（避免循环）

**冷却保护**：连续 3 段都 `tts_failed` → 开发板临时把 `tablet_direct` 关掉走完整段对话，下次对话再尝试（防止平板已离线还狂尝试浪费时间）。

### 段级流式逻辑（决策 3）

平板侧维护一个 segment 队列：
- 收到 `tts_text` 立即开始 fetch（多段并发起 fetch，让网络层 pipeline）
- 但**播放时**按 `segment_id` 顺序：第 N 段没到首块前不开始第 N+1 段的播放
- Web Audio 的 `nextStartTime` 机制保证段内 PCM 拼接平滑（已在 PoC 验证）

### 打断机制（决策 3：后做）

不在本期改造范围。后续做时只需：
- 开发板下发 `{type: "tts_cancel"}`
- 平板侧 abort 所有未完成 fetch + 停止 AudioContext + 清空 segment 队列

---

## 当前性能基线（开发板实测，2026-04-29）

参考 `background02.md`：STT→出声 **1554 ms**（Tier 1 路径，qwen-tts，首段 28-32 字）。

预期 v4 改造完成后：**1554 - 250~340 ≈ 1210-1300 ms**。

---

## 改造之前要做的准备

1. **轮换 API key**：当前 dashscope key（`sk-4529e46f796b46539ba4307d5d4fe5c2`）已在多个测试脚本里硬编码且对话曝光过，正式部署前去 dashscope 控制台换新 key
2. **决定平板用的 key 是否和开发板分开**：建议分开，方便单独限额/审计

---

## v4 之前已做（继承自 background01-02，保留有效）

- 三层路由架构（Tier 0 关键词直达 / Tier 1 qwen-flash / Tier 2 qwen3-coder-next）
- qwen-tts 流式管线 + 段起始 15ms fade-in 抑制爆音 + 纯标点段过滤
- 6 个激活的 MCP 工具（drone.takeoff/land/status, mapping.view, audio_speaker volume）

---

## v4 之后的待办（继承 + 新增）

### P0（本期改造完成后再看）
- 平板直连 TTS 正式落地（**本文档主线**）

### P1
- 切豆包 TTS：**本期已评估，否决**
- Tier 2 直达关键词词表（当前空 list）
- 常态化 ROS Bridge（`subprocess.Popen` → 主进程驻留 publisher 单例）

### P2
- STT 首字延迟（base→tiny / GPU / streaming whisper）
- 段间接缝感（已用 fade-in 解决主要爆音，如果用户后续仍觉接缝明显再做）

### P3
- 带参数关键词（"音量调到 70"）
- 打断机制（决策 3 推迟项）

---

## 协作约定（沿用 v3）

- PC 上不跑项目代码，syncpi 到开发板再测
- 改动前先讨论方案，避免大刀阔斧改完才发现方向不对
- 改完代码后等用户实测日志反馈，再决定下一步
- **平板 UI 改动改 `src/display/web_static/` 下的 JS 即可，APK 通常不动**

---

## 项目内重点文档

- `background01.md` — v2（三层路由架构落地）
- `background02.md` — v3（edge-tts→qwen-tts + 段爆音修复）
- **`background03.md` — 本文档（v4 平板直连 PoC + 改造方案）**
- `FIRST_RESPONSE_LATENCY.md` — 早期方案分析（场景 A/B 对比，部分已落地）
- `SLAM_WEB_VIEWER_DESIGN.md` — SLAM 模块设计

---

## 关键文件位置速查

```
项目根: /home/kian/kian_project/aiagent

后端 TTS:
  src/tts/qwen_tts_client.py              ← qwen-tts 流式客户端（首块埋点 line 204-232）
  src/protocols/local_agent_protocol.py    ← _tts_sink_one_segment 段级推送
  src/display/web_server.py                ← FastAPI + WS（改造要在这里加 tts_text 通道）

平板 UI（开发板托管，平板 WebView 加载）:
  src/display/web_static/index.html        ← 主页面
  src/display/web_static/audio_out.js      ← 当前 mp3 播放（fallback 路径）
  src/display/web_static/tts_poc.html      ← PoC 测试页
  src/display/web_static/tts_poc.js        ← PoC 核心逻辑（直接复用进 tts_direct.js）

平板 APK（极少改动）:
  android_webview/app/src/main/java/com/kian/aiagent/MainActivity.kt
  android_webview/app/src/main/java/com/kian/aiagent/AudioBridge.kt

测试脚本:
  scripts/qwen_tts_latency_test.py
  scripts/volcano_tts_latency_test.py

配置:
  config/config.json                        ← TTS.provider / dashscope_api_key
```

---

## 给下一个 Claude 的话

接力建议：
1. 先看本文档"v4 已敲定的 5 个决策"和"未完成：正式改造方案"
2. 数据基线用本文档的 PoC 数字（283ms 稳态首块）
3. 改造按"改造文件清单"顺序：先改协议（local_agent_protocol + web_server），再写 tts_direct.js（直接复用 tts_poc.js 核心），最后接 fallback
4. 改造完成后用 PoC 页面做回归对比（数字应该一致）
5. **不要重测豆包**（本期已否决，结论别再翻）
