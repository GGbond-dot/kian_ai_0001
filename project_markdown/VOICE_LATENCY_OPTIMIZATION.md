# 平板语音交互 — 首响应延迟优化（归纳总览）

> **性质**：本文是 v2–v5 多轮"接力交接"文档（原 `background01-04.md`）与早期方案分析（原 `FIRST_RESPONSE_LATENCY.md`）的归纳合并，作为单一长期维护文档。
> **状态**：软件层全部已落地实测。场景 A（一般对话）稳态端到端首声 **~1.23s**；场景 B（ROS 语音控制）端到端 ack **~0.57s**。**飞控端是否真收到 UInt8 帧尚未现场验证**。
> **最后更新**：2026-06-03（合并归纳）。

---

## 开发模式（每次开新对话先看）

- PC（Ubuntu 24.04）只写代码、不跑项目（缺运行依赖）；通过 `syncpi` 同步到开发板（Ubuntu 22.04，ARM）运行
- 所有性能数据来自开发板/平板实测；PC 上只允许静态校验（`python3 -c "import ast"`、`node --check`、`json.load`）和 curl 外部 API
- ROS 用 humble 是给开发板的，不是写错
- 平板（华为 MatePad）= 展示 + 麦克风 + 播放端，APK 是 WebView 壳加载 `http://192.168.10.1:8080/`；**平板 UI 实质就是本仓库 `src/display/web_static/` 下的 HTML/JS**，APK 通常不用动
- 改 `web_static/` 下 JS 后记得更新 `index.html` 里 `?v=N`，否则 WebView 跑旧版（已踩坑）

## 项目定位

多无人机协同物流系统的智能终端机器人（嵌入式开发板 + 平板 WebView 展示端）。核心是无人机起飞/降落、SLAM 建图查看。**不是个人桌面助手。**

---

## 当前状态总览

| 场景 | 触发样例 | 基线 | 当前 | 关键手段 |
|---|---|---|---|---|
| A 一般对话 | "你叫什么名字？" | ~5.9s | **~1.23s** | 三层路由 + LLM 流式 + 平板直连云 TTS + 双预热 + qwen3-asr-flash STT |
| B ROS 语音控制 | "起飞""降落""悬停" | ~5.9s | **~0.57s**（STT→ack TTS） | Tier 0 关键词直达跳过 LLM + 常驻 DroneCommandBridge |

两场景正交，可独立优化。

---

## 三层路由架构（v2 落地，沿用至今）

```text
STT 文本
  ↓
[Tier 0] 关键词意图直达（0 LLM，目标 <1s）
  起飞/降落/悬停/看地图/电量 → INTENT_KEYWORDS 命中 → 直接调 MCP 工具
  命令型：先 ack TTS + 异步执行工具；查询型：等工具结果再播报
  ↓ 未命中
[Tier 2 直达] ROUTER.tier2_keywords 命中（当前空 list，留 hook）→ 跳过 Tier 1
  ↓ 未命中
[Tier 1] qwen-flash + 项目背景 prompt + 无 tools
  流式扫描前 30 字，命中 fallback 短语（"做不了/没办法处理/我不会"）→ 静默丢弃 → Tier 2
  否则正常切句推 TTS
  ↓ fallback 触发
[Tier 2] qwen3-coder-next + 完整 tools（原路径）
```

设计要点：
- `ROUTER.fast_path_enabled` 总开关，翻车一键回退老路径；**Tier 0 优先级最高，命中即调工具不走 LLM**，测 LLM 路径需临时关此开关、测完改回
- Tier 1 不走 agent，直接 LLMClient + 拼 history，避免 fallback 时 history 双 append；fallback 前完全不推 TTS，用户不会听到一半切换
- **drone 工具的口语化覆盖率靠扩 `INTENT_KEYWORDS`，不靠改 Tier 2 system_prompt**（Tier 2 是给日志分析/高级决策用的，定位不同；曾实测 Tier 2 LLM "嘴上说调了工具其实没调"，决策不修）

---

## 演进时间线

| 版本 | 阶段成果 | 端到端 |
|---|---|---|
| v2 | 三层路由架构落地；砍工具 40→6；LLM 流式 + 句级 TTS；模型选型实测确定 qwen-flash（TTFB ~332ms） | ~3.86s |
| v3 | edge-tts → qwen-tts（首块 950-1456ms 含偶发 16s SSL reset → 376-533ms 稳定）；段首 15ms fade-in 抑爆音；纯标点段过滤 | 1554ms |
| v4 | 平板直连云 TTS（开发板只推文本，平板自己 fetch dashscope SSE + Web Audio 播放）+ LLM/平板双预热 + qwen3-asr-flash 云 STT（替 whisper base，简繁混淆消失） | ~1.23s |
| v5 | 场景 B bridge 化：DroneCommandBridge 常驻 publisher 单例替代 subprocess 冷启动；新增 drone.hover(value=3)；"停止/停下"归 land；mapping.view 改纯文本不起 rviz2 | ack ~0.57s |

关键发现：**LLM TTFB 是端点固有延迟，不是 prompt 长度**——砍 70% prompt（9908→3005 token）TTFB 没降。qwen3-coder-next 端点 ~1700ms，故 Tier 1 选 qwen-flash。

---

## 当前线上配置

凭据写在 `config/config.json`（key 用环境变量占位）：

| 用途 | base_url | model | key |
|---|---|---|---|
| LLM（Tier 2，需 tool-calling） | `https://coding.dashscope.aliyuncs.com/v1` | qwen3-coder-next | `${DASHSCOPE_CODING_API_KEY}` |
| LLM_FAST（Tier 1） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-flash | `${DASHSCOPE_API_KEY}` |
| TTS | `https://dashscope.aliyuncs.com/api/v1` | qwen3-tts-flash，voice=Cherry | 同 LLM_FAST（dashscope 一个 key 通吃） |
| STT | qwen3-asr-flash（批量），`STT.streaming_enabled:false` | | 同上 |

主要开关：`TTS.provider:"qwen"`、`TTS.tablet_direct:true`（false 回退旧 mp3 路径）、`STT.provider:"qwen"`、`ROUTER.fast_path_enabled:true`。

当前激活 MCP 工具（6+）：`drone.takeoff/land/hover/status`、`mapping.view`、`self.audio_speaker.set_volume/get_volume`。源码保留但不注册：calendar/timer/music/web/camera/screenshot/bazi/system.application/SceneMonitor。

drone value 码表：`1`=起飞、`2`=降落/停止、`3`=悬停；topic `/drone_command`(UInt8)，100ms 周期持续 8s（80 帧），新命令 cancel 旧任务立即覆盖。

---

## 关键契约与约束（改动前必读）

1. **段 id 协议契约**：开发板 `_tablet_segment_id` 进程级累加从不归零；平板 `tts_direct.js` 跟随第一个收到的 id 作为播放起点。改任一侧前先理解，否则回到死锁。
2. **`STT.transcribe_pcm` 是必需接口**：平板麦克风走 PCM 不走 opus，新增 STT provider 必须实现 `transcribe_pcm(pcm_bytes)`，否则被 protocol 静默跳过。
3. **rclpy.init 永远先 `if not rclpy.ok():` 再 init**：进程内 SlamBridge 与 DroneCommandBridge 共享 `rclpy.init()`，无脑 init 会撞 "already called"。
4. **Tier 2 不承担 drone 工具兜底**：扩口语化命令加 `INTENT_KEYWORDS`，不要改严 `LLM.system_prompt`。
5. **mapping.view 已不调 ROS、不起 rviz2**，只返回固定文案；要恢复桌面 rviz2 请新增独立工具，别改 mapping.view 语义。
6. **ROS env 固定在 `main.py` 顶部**用 `os.environ.setdefault("ROS_DOMAIN_ID","10")` / `RMW_IMPLEMENTATION`，保留命令行 export 覆盖能力，别改成直接赋值。
7. **平板 UI fade-in/缓存**：段首 15ms fade-in 放在平板侧（`tts_direct.js`）；静态资源加 `?v=N`、HTML 加 no-cache 头防 WebView 缓存。
8. **PoC 保留勿删**：`tts_poc.html` / `tts_poc.js` + `/tts_poc` 路由作回归对比。

---

## 已否决的方案（不要重做）

| 方案 | 否决原因 |
|---|---|
| 豆包（火山引擎）TTS | v4 实测延迟/音色持平 qwen-tts，不切。鉴权确认：只需 `X-Api-Key`+`X-Api-Resource-Id`，endpoint `openspeech.bytedance.com/api/v3/tts/unidirectional/sse` |
| paraformer-realtime 流式 STT | 短句反而慢 ~700ms（finalize ~1s 内置后处理延迟）。代码保留，开关默认关，长句口述场景才值得重评 |
| Tier 2 LLM 兜底 drone 工具 | 会污染 Tier 2 定位，且实测"嘴上调实际没调" |
| 二次确认作默认行为 | 影响体验；高风险命令靠 config 开关 `INTENT_CONFIRM_TAKEOFF` 选择性启用，安全网应在飞控侧（RC override / 失联自动降落） |

---

## 待办与未实测项

**场景 B 待开发板实测（下次 syncpi 后，按优先级）：**
1. 飞控真收到帧：另开终端 `ros2 topic echo /drone_command`，说"起飞"看是否 echo `data: 1`
2. 覆盖语义：连说"起飞"→"降落"，看 8s persist 任务是否被 cancel（value 立即 1→2）
3. 悬停 echo `data: 3`；mapping.view 说"看地图"不起 rviz2
4. 改造前后端到端对比（强制 bridge 不可用走 subprocess fallback）
5. 多 Node 共存压测（bridge + /slam 持续看图 30s+）

**延迟可继续压榨（理论下限 ~700-900ms）：**
- Tier1 启动延迟微调：`ROUTER.fallback_scan_chars` 30→15 省 ~80-100ms（代价：fallback 短语只在前 15 字有效）
- 段间接缝：force_max_len 调大少切一次 / 合并 <4 字极短段 / 前端 MSE 拼接 / 终极 PCM 直推 AudioWorklet
- 打断机制（本期不做）：开发板下发 `{type:"tts_cancel"}` → 平板 abort fetch + 停 AudioContext + 清队列

---

## 安全

- **dashscope API key 已在多个测试脚本硬编码且对话曝光过，正式部署前必须去控制台轮换。**
- `TTS.tablet_api_key` 已预留为独立字段（当前值同后端），建议正式部署时与开发板分开，方便单独限额/审计。

---

## 关键文件速查

```
后端 TTS / 协议:
  src/protocols/local_agent_protocol.py   ← 三层路由 / _tts_sink_one_segment / on_tablet_audio_out_text / 预热
  src/protocols/intent_matcher.py          ← Tier 0 关键词匹配
  src/tts/qwen_tts_client.py               ← qwen-tts 流式客户端（mp3 fallback 路径用）
  src/llm/llm_client.py / agent.py         ← 流式 + 双实例（config_section）
  src/stt/qwen_asr_stt.py / qwen_stream_stt.py
  src/display/web_server.py / web_display.py  ← /ws/audio_out 双向

场景 B（ROS）:
  src/ros/drone_command_bridge.py          ← 常驻 publisher 单例 + 独立 executor 线程
  src/mcp/tools/robot_dispatch/tools.py    ← _publish_int_fire_and_forget 走 bridge（保留 subprocess fallback）
  src/mcp/tools/robot_dispatch/manager.py  ← 工具注册
  scripts/ros2_int32_publisher.py          ← 保留作 fallback，不删
  main.py                                  ← os.environ.setdefault ROS_DOMAIN_ID/RMW

平板 UI（开发板托管，平板 WebView 加载）:
  src/display/web_static/index.html        ← 脚本带 ?v=N
  src/display/web_static/tts_direct.js     ← tts_text → 直连 dashscope（v4）
  src/display/web_static/audio_out.js      ← 旧 mp3 fallback
  src/display/web_static/tts_poc.{html,js} ← 回归 PoC，勿删

配置: config/config.json
```

---

## 关联文档

- `SLAM_WEB_VIEWER_DESIGN.md` — SLAM 模块框架设计
- `slam_base_map_and_nfz_design.md` — 基底图叠加 / 禁飞区设计（含 P0-P2 实施记录）
- `slam_grasp_region_design.md` — 框选抓取任务对接方案
- `WEB_UI_ARCHITECTURE_PLAN.md` — Web UI 远程渲染架构
