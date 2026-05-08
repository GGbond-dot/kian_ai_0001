# 物流系统终端机器人 — 首响应延迟优化（接力交接 v2）

## ★ 开发模式（必须明确，最重要！）

双设备开发：

- PC（Ubuntu 24.04）只写代码，缺项目运行依赖
- 通过 syncpi 同步到开发板（Ubuntu 22.04）运行
- 所有性能数据来自开发板实测，不要尝试在 PC 本地启动/测试
- ROS humble 是给开发板的，不是写错
- 任何"测试一下"都意味着请用户 syncpi 后跑，等用户告诉你结果
- PC 上 curl 外部 API（dashscope 等）可以做，那是网络请求不是项目代码

---

## 项目定位

多无人机协同物流系统的智能终端机器人（嵌入式开发板 + 平板 WebView 展示端），核心是无人机起飞/降落、SLAM 建图查看。不是个人桌面助手（fork 自 py-xiaozhi 已大幅瘦身）。

---

## 已完成的优化路径

### 阶段时延演变

| 阶段 | 首响应 | 主要改动 |
| --- | --- | --- |
| 基线 | 5.9s | — |
| LLM 流式 + 句级 TTS | ~3.86s | agent.run_streaming() |
| 砍工具 + warmup 修复 | ~3.97s | 40 → 6 个工具 |
| 三层路由（本次） | 待开发板实测 | Tier 0 / Tier 1 / Tier 2 |

### 关键发现：LLM TTFB 是端点限制，不是 prompt 长度

实测：砍 70% prompt（9908 → 3005 token）TTFB 没降（1700ms → 1700ms）。说明 prefill 不是大头，模型路由 + 首 token 生成才是 dashscope coding.dashscope.aliyuncs.com 端点（qwen3-coder-next）的固有延迟。

### 模型选型实测（PC 端 curl，非项目代码）

| 模型 | TTFB 中位数 | 备注 |
| --- | --- | --- |
| qwen-flash | 332ms | ✅ 选用，便宜稳定 |
| qwen-turbo | 366ms | 持平 |
| qwen-plus | 550-625ms | 慢 |
| qwen-long | 700-770ms | 慢 |
| qwen2.5-3b/7b-instruct | 392-647ms | 方差大 |
| qwen3-coder-next（旧路径） | ~1700ms | 端点固有延迟 |

---

## 本次落地的三层路由架构

```text
STT 文本
  ↓
[Tier 0] 关键词意图直达（0 LLM，目标 <1s）
  起飞/降落/地图/电量 → 直接调 MCP 工具
  命令型：先 ack TTS + 异步执行工具
  查询型：等工具结果 → 播报
  ↓ 未命中
[Tier 2 直达] tier2_keywords 命中（当前空 list，留 hook）
  → 跳过 Tier 1 直接 coder-next + tools
  ↓ 未命中
[Tier 1] qwen-flash + 项目背景 prompt + 无 tools
  流式扫描前 30 字符
  命中 fallback 短语（"做不了/没办法处理/我不会"）→ 静默丢弃 → Tier 2
  否则正常切句推 TTS
  ↓ fallback 触发
[Tier 2] qwen3-coder-next + 完整 tools（原路径）
```

### 改动文件清单

| 文件 | 改动 |
| --- | --- |
| config/config.json | 加 LLM_FAST / INTENT_KEYWORDS / ROUTER 三段 |
| src/llm/llm_client.py | __init__(config_section="LLM") 支持双实例 + model property |
| src/protocols/intent_matcher.py | 新文件：match_intent() / match_tier2_direct() |
| src/protocols/local_agent_protocol.py | _run_agent_pipeline_after_stt 重写为三层；新增 _run_tier0_intent / _run_tier1_fast / _run_tier2_full |

### 关键设计决策

- Tier 1 不走 agent：直接用 LLMClient + 拼装 history，避免 fallback 时 history 双 append
- Tier 1 fallback 前完全没推 TTS：用户不会听到一半切换
- ROUTER.fast_path_enabled 开关：翻车一键回退老路径
- _llm_fast 单例懒加载：首次 Tier 1 才构造
- Tier 0 命令型用 asyncio.create_task 异步执行工具：不阻塞 ack 播放

---

## 已就位的代码（之前阶段 + 本次）

### LLM 层

- src/llm/llm_client.py — chat_completion(stream=True) 返回 AsyncStream，含 prompt token 估算 + TTFB 埋点；支持 config_section 双实例
- src/llm/agent.py — run_streaming() 异步生成器，工具调用轮非流式 / 文字轮流式

### 协议层

- src/protocols/local_agent_protocol.py
  - _take_complete_segment() 增长缓冲按强标点切句
  - _tts_sink_one_segment() 单段合成+推送
  - _consume_llm_stream_to_remote_tts() 消费 token 流即时推 TTS
  - _kick_edge_tts_warmup() 与 LLM 并行做 TLS 握手（warmup 文本必须有可朗读字符，"。" 不行用 "嗯。"）
  - _clean_text_for_tts() 已扩展剥离 ～——•★ 等装饰符号

### 配置

- STT.vad_min_silence_duration_ms: 300（从 500）
- LLM.optional_tool_groups: [] —— 改成 ["calendar","timer","music","web"] 任一组就恢复

---

## 当前激活的工具

```text
drone.takeoff drone.land drone.status mapping.view self_audio_speaker_set_volume self_audio_speaker_get_volume
```

源码保留但不注册的：calendar / timer / music / web / camera（和 SceneMonitor）/ screenshot / bazi / system.application

---

## 待办（下一阶段优先级）

### P0 — 开发板实测三层路由

把 PC 上写的代码 syncpi 到开发板，验证三个场景：

1. Tier 0：说"起飞" / "电量"
   - 期望日志：[Tier0] 命中关键词 ...
   - 期望首响应：< 1s
2. Tier 1：说"今天心情不错" / "你叫什么"
   - 期望日志：[Tier1] flash 完成 总耗时=Xms
   - 期望首响应：~1.5-2s（TTFB 332ms + TTS 950ms + 切句开销）
3. Tier 1 → Tier 2 fallback：说"分析一下今天的飞行日志"
   - 期望日志：[Tier1] 命中 fallback 短语 ... → Tier 2
   - 期望走原 coder-next 路径

根据实测数据决定后续动作。

### P1 — 填 Tier 2 直达关键词词表（依赖 P0 实测结论）

现在 ROUTER.tier2_keywords = [] 空。等用户测完 Tier 1 实际能力边界后，把 flash 答不出的高级语义类关键词填进去（候选：日志/分析/调度/对比/汇总/故障/规划），跳过 Tier 1 直接走 Tier 2 省 ~400ms fallback 开销。

词表尺度原则：宁可漏（高级问题误走 Tier 1 → fallback 多 400ms）不可错（闲聊误走 Tier 2 → 慢 1.3s）。不放"怎么/如何"等闲聊也常用的词。

### P1 — 常态化 ROS Bridge

src/mcp/tools/robot_dispatch/tools.py:_publish_int_fire_and_forget 每次 subprocess.Popen 启 Python + import rclpy，冷启动 1-1.5s。改造为主进程驻留 publisher 单例（rclpy 已经在主进程跑 SLAM bridge）。和 Tier 0 配套，让"起飞"真正达到 0.7s。

### P2 — qwen-tts 实测（可能不做）

edge-tts 段 0 首块 ~950ms 是当前 TTS 瓶颈。src/tts/qwen_tts_client.py 已存在但只产 Opus 帧，要先加 WAV 输出 + 实测首块延迟。

- qwen 首块 < 500ms → 切
- ≈ 800ms → 不切
- < 300ms → 进一步上 qwen-tts 真流式

### P3 — 带参数关键词（暂不做）

如"音量调到 70" / "起飞到 5 米"。开始引入复杂度，等核心三层路由稳了再加。

---

## 关键埋点日志（验证改动用）

```text
[工具列表] 共 N 个工具传给 LLM：[...]
[LLM/prompt] 估 token: msg=X tool=Y total≈Z
[LLM/timing] POST→响应对象 Xms
[LLM/timing] 首 chunk 到达 Xms
[LLM/timing] 首文字 token Xms
[TTS/warmup] 已触发预热任务 → edge-tts 预热完成 Xms
[TTS/stream] 段 N ok 字数=X 首块=Yms 合成=Zms
[TTS/stream] 首段已推送 距 LLM 起=Xms

# 三层路由新增：
[Tier0] 命中关键词 'XXX' → 工具 drone.takeoff ack='好的，正在起飞'
[Tier0] 工具 drone.takeoff 完成: ...
[Tier1] flash 完成 总耗时=Xms 字数=Y 回复='...'
[Tier1] 命中 fallback 短语 '做不了' (前 N 字)，丢弃 flash 输出
[Tier2] 触发原因=tier2-fallback-or-default
[Tier2-direct] 命中关键词 'XXX'，跳过 Tier 1
```

---

## 已修复但需在新硬件上验证

- 摄像头报错刷屏（已下线 SceneMonitor）
- TTS warmup 失败（"。" → "嗯。"）

---

## 项目内重点文档

- project_markdown/FIRST_RESPONSE_LATENCY.md — 完整方案文档（Part A 一般对话 + Part B ROS 控制 + 决策记录）
- project_markdown/README.md — 项目入口
- project_markdown/SLAM_WEB_VIEWER_DESIGN.md — SLAM 模块设计

---

## API 凭据（写在 config/config.json 里）

- LLM（Tier 2，coder-next）：https://coding.dashscope.aliyuncs.com/v1 + sk-sp-ac3c84c5e49946ceb4fe63fbee8f9787 + model qwen3-coder-next
- LLM_FAST（Tier 1，flash）：https://dashscope.aliyuncs.com/compatible-mode/v1 + sk-4529e46f796b46539ba4307d5d4fe5c2 + model qwen-flash

---

## 协作约定

- PC 上不跑项目代码，syncpi 到开发板再测
- 改动前先讨论方案，避免大刀阔斧改完才发现方向不对
- 改完代码后等用户实测日志反馈，再决定下一步
