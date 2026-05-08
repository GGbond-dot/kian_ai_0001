# 物流系统终端机器人 — 首响应延迟优化（接力交接 v3）

## ★ 开发模式（最重要！必须先看）

**双设备开发：**

- **PC（Ubuntu 24.04）** 只写代码，**没有项目运行依赖**，**绝对不要尝试在 PC 本地启动/测试**
- 通过 **syncpi** 把代码同步到**开发板（Ubuntu 22.04，ARM）** 运行
- 所有性能数据、日志都来自开发板实测，PC 上不要 `python3 main.py`
- ROS humble 是给开发板的，不是写错
- 任何"测试一下"都意味着请用户 syncpi 后跑，等用户贴日志反馈
- PC 上 curl 外部 API（dashscope 等）可以做，那是网络请求不是项目代码
- 平板（华为 MatePad）= 展示 + 麦克风采集 + 音频播放端，通过 WebSocket 连开发板

---

## 项目定位

多无人机协同物流系统的智能终端机器人（嵌入式开发板 + 平板 WebView 展示端）。
核心：无人机起飞/降落、SLAM 建图查看。
不是个人桌面助手（fork 自 py-xiaozhi 已大幅瘦身）。

---

## v3 阶段（本次）已完成

### 核心改动：edge-tts → qwen-tts

**痛点**：edge-tts 走 Bing 公网 WSS（`speech.platform.bing.com`），国内开发板偶发 SSL `Connection reset by peer`，撞一次就是 16s 卡死。首块 950-1456ms 也偏慢。

**方案**：换阿里 dashscope qwen-tts（与 LLM 同 endpoint 同账号）。

| 指标 | 改前（edge-tts） | 改后（qwen-tts） |
| --- | --- | --- |
| 首块延迟 | 950-1456ms（含偶发 16s SSL reset） | **376-533ms** 稳定 |
| 网络稳定性 | ⚠️ 公网 WSS 偶发翻车 | ✅ 国内 endpoint 零翻车 |
| 首段距 Tier1 起 | 20809ms（首轮）/ 1827ms（稳态） | **1554ms** 首轮即稳定 |

### 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `src/tts/qwen_tts_client.py` | 新增 `stream_pcm_chunks()` 异步生成器（流式 SSE 边收边吐 PCM） + `synthesize_to_mp3()` 真流式管线（PCM 边来边喂 ffmpeg）+ 首块延迟埋点 + **15ms fade-in 抑制段起始爆音** |
| `src/protocols/local_agent_protocol.py` | `_synthesize_mp3_for_remote_timed` 按 `TTS.provider` 分发（qwen / edge）；`_can_use_streaming_remote_tts` 白名单加 qwen；`_tts_sink_one_segment` 加"必须有可朗读字符"过滤（避免纯标点段浪费 ~500ms API 调用） |
| `config/config.json` | `TTS.provider: "qwen"` + 填入 `dashscope_api_key`（用 LLM_FAST 那个 key，账号通用） |
| `config/user_memory.json` | 清空 `conversation_summary` / `recent_history` / `summary_history` / `explicit_memories`（旧"卖手机"测试污染了对话上下文，导致 Tier 1 回复跑偏，备份在 `.bak`） |

### 修复的并发 bug

1. **Tier 1 路由对 qwen 失效**：`_can_use_streaming_remote_tts` 写死了 provider 白名单只允许 edge-tts，导致 provider=qwen 时整个 Tier 1 + 句级流式管线被绕过，所有请求掉到 Tier 2（coder-next）多花 ~600ms。已修。
2. **段起始爆音**：每段独立 MP3 开头有 "噗" 声。原因是 libmp3lame priming 样本 + PCM 边界阶跃跳变。ffmpeg 加 `-af afade=t=in:st=0:d=0.015` 解决。
3. **纯标点段 API 浪费**：切句切出 "）" 一个全角右括号，qwen-tts 拒合成纯标点但仍消耗 504ms HTTP。`_tts_sink_one_segment` 加 `re.search(r"[一-鿿0-9A-Za-z]")` 拦下来。

---

## 当前性能基线（开发板实测，2026-04-29）

```text
说"你好呀你好呀" → 首段出声链路：

STT 转写完成   t = 0
Tier1 起算     t + 1ms      （进 _run_tier1_fast）
LLM 响应对象   t + 541ms    （qwen-flash TTFB）
qwen-tts 首块  t + 1305ms   （首段 PCM 第一块到，距 LLM 响应 ~764ms 含切句等待）
首段已推送     t + 1554ms   ★ 端到端从 STT→平板出声

后续段稳态：每段 7-32 字，首块 376-446ms，无翻车。
```

---

## v3 之前已做（保留有效）

### 三层路由架构（v2 落地）

```text
[Tier 0] 关键词意图直达（0 LLM）
  起飞/降落/地图/电量 → 直接调 MCP 工具
  ↓ 未命中
[Tier 2 直达] tier2_keywords 命中（当前空 list）
  ↓ 未命中
[Tier 1] qwen-flash + 项目背景 prompt + 无 tools
  流式扫描前 30 字，命中 fallback 短语 → 静默丢弃 → Tier 2
  ↓ fallback 触发
[Tier 2] qwen3-coder-next + 完整 tools
```

### LLM TTFB 端点对比（PC curl 实测，仅作选型参考）

| 模型 | TTFB 中位数 |
| --- | --- |
| **qwen-flash** | **332ms** ✅ Tier 1 选用 |
| qwen-turbo | 366ms |
| qwen-plus | 550-625ms |
| qwen3-coder-next | ~1700ms ⚠️ Tier 2 必要的 tool-calling 端点 |

### 现有埋点日志清单

```text
[Tier0] 命中关键词 'XXX' → 工具 ...
[Tier1] flash 完成 总耗时=Xms 字数=Y 回复='...'
[Tier1] 命中 fallback 短语 ...
[Tier2] 触发原因=...
[QwenTTS/stream] 首块到达 Xms text_len=Y      ← v3 新增
[TTS/stream] 段 ok 字数=N 首块=Xms 合成=Yms 推送=Zms
[TTS/stream] 首段已推送 距 Tier1 起=Xms
[TTS/stream] 段无可朗读内容，跳过 seg=...     ← v3 新增
[LLM/timing] POST→响应对象 Xms
[LLM/prompt] 估 token: msg=X tool=Y total≈Z
```

### 当前激活的 MCP 工具（6 个）

```text
drone.takeoff  drone.land  drone.status  mapping.view
self.audio_speaker.set_volume  self.audio_speaker.get_volume
```

源码保留但不注册：calendar / timer / music / web / camera / screenshot / bazi / system.application / SceneMonitor

---

## 待办（按优先级）

### P0 — 段间接缝感（用户反馈次要痛点）

虽然爆音已用 fade-in 解决，但**多段 MP3 串播本质上有接缝**（解码器重启 + qwen-tts 段级合成韵律不连续）。如果用户后续仍觉得"一段段感"明显，备选方案：

- **A 短期**：把 `_take_complete_segment` 的 `force_max_len` 从 60 提到 100，少切一次接缝就少一处。代价：首段稍晚（多等几个字）。**先做这个**。
- **B 中期**：合并 < 4 字的极短段到下一段（避免"你好呀～"被独立切出来）。
- **C 长期**：**前端 MSE（MediaSource Extensions）拼接**——平板把多段 mp3 喂同一个 MediaSource，浏览器解码器连续工作，**接缝完全消除**。需要平板仓库 `kian_ai_0001` 配合改 JS。
- **D 终极**：开发板推 raw PCM chunks，平板 AudioWorklet 直接喂扬声器。改造量大，零接缝零延迟。

### P0 — 平板端 TTS 架构迁移（潜在大优化）

当前架构：开发板调云 TTS → 推 mp3 给平板。
更优架构：**开发板只推文本，平板自己调云 TTS**（豆包/qwen-tts）。

收益：
- **开发板 CPU 降为 0**（去掉 ffmpeg 转码 + httpx 拉流）
- 链路减少一跳同步等待，**首段可能再省 100-300ms**
- 华为平板 wifi 6 网络往往比开发板更快

约束：要改 WebSocket 协议（推文本而非 mp3），平板仓库要配合改前端。**做之前先和用户确认是否要动平板代码。**

### P1 — 切到豆包（火山引擎）TTS

如果嫌 qwen-tts 首块 ~450ms 还慢：
- 豆包 TTS 真流式 WebSocket，首块 **150-250ms**
- 国内 CDN 比 dashscope 还快，音色更丰富
- 缺点：要新开火山账号，单独计费

### P1 — Tier 2 直达关键词词表

`ROUTER.tier2_keywords = []` 当前空，等积累更多 Tier 1 失败案例后填进去（候选：日志/分析/调度/对比/汇总/故障/规划），高级语义直接走 Tier 2 省 ~400ms fallback 开销。

**词表尺度原则**：宁可漏（高级问题误走 Tier 1 → fallback 多 400ms）不可错（闲聊误走 Tier 2 → 慢 1.3s）。不放"怎么/如何"等闲聊也常用的词。

### P1 — 常态化 ROS Bridge

`src/mcp/tools/robot_dispatch/tools.py:_publish_int_fire_and_forget` 每次 `subprocess.Popen` 起 Python + import rclpy，冷启动 1-1.5s。改造为主进程驻留 publisher 单例（rclpy 已经在主进程跑 SLAM bridge）。和 Tier 0 配套，让 "起飞" 真正达到 0.7s。

### P2 — STT 首字延迟（潜在中等收益）

当前 STT 用 faster-whisper base + CPU + int8。
- 录音停止到 STT 出结果：实测 ~0.5-1.0s
- 进一步压：换 tiny 模型 / GPU（开发板有没有 GPU 看硬件）/ streaming whisper（边录边转）
- 相比 LLM+TTS 加起来 1s+，STT 已不是大头，但能再压 200-300ms

### P3 — 带参数关键词（暂不做）

如"音量调到 70" / "起飞到 5 米"。引入 NER 复杂度，等核心三层路由稳了再加。

---

## 还能压榨的延迟（首响应时间预算分析）

当前基线 **STT→出声 ≈ 1554ms**（Tier 1 路径）。预算拆解：

| 环节 | 当前耗时 | 理论下限 | 怎么压 |
| --- | --- | --- | --- |
| LLM TTFB | 541ms | ~330ms | qwen-flash 是已优中之优；换更小模型音质风险 |
| LLM token → 切第一段 | ~200ms | ~100ms | 把 `force_max_len` 调小，但与"减接缝"目标矛盾 |
| qwen-tts 首块 | 446ms | ~200ms | 换豆包 / 走平板端直连 |
| ffmpeg PCM→MP3 编码 | ~50-100ms | 0 | 改协议直接推 PCM 给平板，AudioWorklet 解码 |
| WebSocket 推送+平板播放 | ~50ms | ~10ms | 已很短，不值得动 |

**乐观下限：~700-900ms**（需做完 P0 平板端 TTS + P1 豆包 + 终极 PCM 直推）。
**保守压榨：~1100ms**（仅做 P1 豆包，不动协议）。

---

## API 凭据（写在 `config/config.json` 里）

- **LLM**（Tier 2，coder-next，需 tool-calling）：
  - base_url: `https://coding.dashscope.aliyuncs.com/v1`
  - api_key: `sk-sp-ac3c84c5e49946ceb4fe63fbee8f9787`
  - model: `qwen3-coder-next`
- **LLM_FAST**（Tier 1，flash）：
  - base_url: `https://dashscope.aliyuncs.com/compatible-mode/v1`
  - api_key: `sk-4529e46f796b46539ba4307d5d4fe5c2`
  - model: `qwen-flash`
- **TTS**（qwen-tts）：
  - base_url: `https://dashscope.aliyuncs.com/api/v1`
  - api_key: 同 LLM_FAST（账号通用，dashscope 一个 key 通吃）
  - model: `qwen3-tts-flash`
  - voice: `Cherry`

---

## 项目内重点文档

- `project_markdown/background01.md` — v2 阶段交接（三层路由架构落地）
- `project_markdown/background02.md` — **本文档**（v3 qwen-tts 切换 + 段爆音修复）
- `project_markdown/FIRST_RESPONSE_LATENCY.md` — 完整方案文档（一般对话 + ROS 控制）
- `project_markdown/SLAM_WEB_VIEWER_DESIGN.md` — SLAM 模块设计

---

## 协作约定

- **PC 上不跑项目代码**，syncpi 到开发板再测
- 改动前先讨论方案，避免大刀阔斧改完才发现方向不对
- 改完代码后等用户实测日志反馈，再决定下一步
- 平板端代码在另一个仓库 `github.com/GGbond-dot/kian_ai_0001`，**改前端前先和用户确认**

---

## 当前已知遗留问题

- 退出时 SlamBridge `rcl_shutdown already called on the given context` 报错（双重 shutdown）。不影响功能，但日志里有红错。
- 平板麦克风首帧"端到端延时=-266ms"是负数（时钟不同步），仅显示问题。
- `pynput` 在无 X server 环境下载入失败 → ERROR 日志一行。开发板无 X 是常态，可忽略。
- `sherpa_onnx` 未安装 → 唤醒词检测器 ERROR 日志。当前不需要唤醒词，可忽略。
