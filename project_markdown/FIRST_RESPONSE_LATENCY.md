# 平板语音交互 — 首响应时延分析与优化方案

> **背景**：本项目采用**双设备开发模式** —— PC（Ubuntu 24.04）写代码，通过 `syncpi` 同步到开发板（Ubuntu 22.04）运行。所有性能数据均来自开发板实测，PC 上不跑项目代码（缺依赖）。  
> **本文性质**：方案讨论文档，不是设计定稿。回到开发板后按各部分的优先级顺序改代码 + 验证。

---

# 两种首响应场景（互相正交，可独立优化）

| 场景 | 触发样例 | 当前基线 | 主要瓶颈 | 优化方向 |
|---|---|---|---|---|
| **A. 一般对话** | "你叫什么名字？" | ~5.9 s | LLM 全包 + TTS 冷启动 | LLM 流式 + TTS 预热/换 provider |
| **B. ROS 语音控制** | "起飞"、"降落"、"看地图" | ~5.9 s | LLM 完全多余 + ROS 进程冷启动 | 跳过 LLM + 常态化 ROS bridge |

两个场景**不冲突**，分别有独立的 P0；最终改造可以并行做。

---

# Part A：一般对话首响应

> **目标**：从用户说完话到平板播放出第一声的间隔。  
> **当前基线**：~5.9 s。

## A.1 当前链路 & 实测耗时

参考日志：`2026-04-28 16:59:33–42` 一次完整对话（用户问"你的名字叫什么呢?"，LLM 回复 70 字）。

| # | 阶段 | 实测/推算 | 数据来源 |
|---|---|---|---|
| 1 | VAD 判定语音结束 | ~500 ms | 配置默认值 `STT.vad_min_silence_duration_ms=500` |
| 2 | PCM 经 WS 上传到开发板 | 23–70 ms | 之前会话实测 |
| 3 | Whisper STT 转写整段 | ~600 ms | 日志 `STT 转写结果` 时间差 |
| 4 | **LLM 推理（非流式 70 字）** | **~2260 ms** | 日志 `Agent 完成` − STT 完成 |
| 5 | TTS 首段合成（edge-tts 冷启动） | ~2440 ms | 日志 `[TTS/remote] 段 0 ok` |
| 6 | WS 推送 mp3 + 平板解码播放 | ~50 ms | 推送日志 `推送=0~2ms`，解码估值 |
| | **端到端首响应** | **~5.9 s** | |

**LLM 与 TTS 总占 ~80%，是优化重点。**

---

## A.2 各环节根因（含代码定位）

### 2.1 VAD 阈值偏保守
- 位置：`src/stt/whisper_stt.py:33-36`
- 配置：`STT.vad_min_silence_duration_ms=500`
- 问题：等用户说完后再静音 500 ms 才触发 STT，中文短句这个阈值偏长

### 2.2 STT 是整段一次转写
- 位置：`whisper_stt.py:174` `transcribe_pcm` → `run_in_executor(_sync_transcribe_pcm)`
- 问题：必须收完整段 PCM 才能开始转写；模型是 `base`

### 2.3 LLM 完全非流式 ★ 主要瓶颈
- 位置：`src/llm/llm_client.py:234`，参数 `stream: bool = False`
- 调用点：`src/llm/agent.py:112` 和 `agent.py:203`，**从未传 `stream=True`**
- 影响：必须等 70 字全部生成完才返回，~2260 ms 是死等
- 端到端代价：`_run_agent_pipeline_after_stt` 里 `await agent.run(...)` 完全阻塞 TTS（`local_agent_protocol.py:767-779`）

### 2.4 TTS 等 LLM 完整回复才启动
- 位置：`local_agent_protocol.py:779` `await self._play_tts_any(reply)`
- 问题：LLM 和 TTS **完全串行**，没有任何流水线重叠

### 2.5 edge-tts 冷启动 ★ 次要瓶颈
- 位置：`local_agent_protocol.py:_synthesize_mp3_for_remote_timed`
- 实测：段 0（3字）首块 2173 ms，段 1（16字）首块 929 ms → **冷启动单独贡献 ~1.2 s**
- 原因：每次 `edge_tts.Communicate` 都重做 TLS + WSS 协商，连接不复用

### 2.6 已排除项（不要在这上面再花时间）
- **第二次 chat/completions 不影响主路径**：来源是 `agent.py:381 _refresh_memory_summary`，由 `asyncio.create_task` 异步启动（line 363），不阻塞 `_play_tts_any`。日志可见但不计入首响应。
- **平板 TTS 不响**：之前是 syncpi 后旧后端进程没重启，新加的 `/ws/audio_out` 路由没生效。已修复（重启即可）。
- **音频上行链路**：23–70 ms 已经够好，不要碰。

---

## A.3 优化方案矩阵

按"首响应净收益 / 工作量"排序：

| 优先级 | 方案 | 首响应预期 | 净收益 | 工作量 | 风险 |
|---|---|---|---|---|---|
| **P0** | LLM 流式 + 句级 TTS 触发 | 5.9 s → 2.7 s | **−3.2 s** | 中（1 天） | tool_calls 路径要兼容 |
| **P1** | edge-tts 启动预热 | 2.7 s → 2.0 s | −0.7 s | 极小（10 行） | 启动多发一次空合成 |
| **P1** | VAD 阈值 500 → 300 ms | 减 200 ms | −0.2 s | 改 1 行 config | 短停顿可能误触发 |
| **P2** | 切 qwen-tts（先做首块实测） | 2.0 s → ~1.4 s ?? | 待实测 | 小（路径 1，加 WAV 输出） | 首块延迟未知 |
| **P3** | TTS 真流式（前端 MSE） | 减 200 ms | −0.2 s | 中（前端重写） | 浏览器兼容、错误处理 |
| **P3** | STT 流式或换更快模型 | 减 200–400 ms | −0.3 s | 大 | 模型质量取舍 |

**理论可达极限（叠加 P0+P1+P2）：~1.4 s**，距离当前 5.9 s 砍 76%。

---

## A.4 P0 详细方案：LLM 流式 + 句级 TTS 触发 ⭐

### 4.1 改造后的链路时序

```
说完话
 ├─ 500 ms (VAD)            ← P1 可压到 300
 ├─  70 ms (上传)
 ├─ 600 ms (STT)
 ├─ 400 ms (LLM 首 token)   ← 关键：不再等 2260ms
 ├─ 200 ms (攒到第一个句末标点)
 ├─ 900 ms (TTS 首段, warm) ← P1 可压到 500
 ├─  50 ms (推送 + 解码)
 = ~2.7 s 首响应（不叠加 P1）
 = ~2.0 s 首响应（叠加 P1）
```

### 4.2 改造点（已扫描定位）

#### A. `src/llm/llm_client.py`
- 当前：`chat_completion` 在 `stream=False` 时 `await client.chat.completions.create(**kwargs)` 返回完整 `ChatCompletion`（line 279）
- 改造：当 `stream=True` 时返回 `AsyncIterator[ChatCompletionChunk]`，调用方按 chunk 累加 `delta.content` 与 `delta.tool_calls`
- 注意：`uses_responses_api()` 路径走的是 `client.responses.create`（line 226），其流式事件类型不同（Responses API 用 `response.output_text.delta` 事件流），需要单独适配

#### B. `src/llm/agent.py` 的 `run()` 主循环
两处改造（line 107 和 line 201，对应 chat_completions 和 responses 两套 API）：
- **仅在没有 tool_calls 的"最终轮"用流式**——工具调用轮 LLM 返回的是 JSON 调用格式，整个调用块到位才能执行，流式没收益反而麻烦
- 改造思路（chat_completions 路径）：
  ```python
  # 先非流式判定本轮是否有 tool_calls：开 stream=True 拿首 chunk 看 delta.tool_calls
  # 有 → 收完整段，按现状执行工具
  # 无 → 把后续 chunk 通过 callback / async generator 推出去
  ```
- 接口建议：新增 `agent.run_streaming(user_input, tools, tool_executor, on_segment)` 或让 `run()` 返回 `AsyncIterator[str]`（句子粒度）。倾向于**新增方法不动现有 `run()`**，避免破坏 `_run_text_pipeline` 等现有调用方。

#### C. `src/protocols/local_agent_protocol.py`
- `_run_agent_pipeline_after_stt` (line 746) 当前：
  ```python
  reply = await agent.run(...)
  await self._play_tts_any(reply)
  ```
- 改造为：
  ```python
  buffer = ""
  async for token in agent.run_streaming(...):
      buffer += token
      while sent := self._extract_complete_sentence(buffer):
          buffer = buffer[len(sent):]
          await self._tts_sink_one_segment(sent)
  if buffer.strip():
      await self._tts_sink_one_segment(buffer)  # 残余尾巴
  ```
- 句子提取：复用 `_split_for_tts` 的逻辑，但要做"流式版本" —— 只在遇到 `。！？!?；;\n` 时切出，没遇到不切（避免半句）
- 长尾保护：如果 LLM 一句很长且无标点（如代码、列表），可以加"超过 N 字强制切"

### 4.3 边角问题

- **tool_calls 链路不动**：工具调用本就需要完整 JSON，沿用非流式路径
- **流式 + memory summary 后台任务**：summary 任务跟主路径异步，不冲突
- **错误处理**：流式中途 SSE 断开 → fallback 到非流式重试一次（兜底而非常态）
- **TTS 已就绪的句级推流**：当前 `_play_tts_any` 远端分支已经是按段循环（之前会话改过），可以拆出 `_tts_sink_one_segment` 给流式消费者复用

---

## A.5 P1/P2/P3 方案细节

### 5.1 P1-A edge-tts 预热
- 在 `WebServer` 启动 / 第一个 audio_out 客户端连上时，后台跑一次 `edge_tts.Communicate("。", voice).stream()` 把 TLS 握手做掉
- 注意：edge-tts 不复用底层连接，预热只对**接下来一小段时间内**的首次合成生效；保险做法是让 `_synthesize_mp3_for_remote_timed` 在每次正式合成前打一次极短预热（异步，不阻塞）

### 5.2 P1-B VAD 阈值
- `config/config.json` → `STT.vad_min_silence_duration_ms: 500 → 300`
- 风险：用户说话节奏稍慢可能被切断；建议 300/400 各试一次取折中

### 5.3 P2 qwen-tts 切换
- 现有 `src/tts/qwen_tts_client.py` 输出 24 kHz PCM 走 Opus 帧，**没有 mp3/wav 输出方法**，不能直接接 audio_out
- 改造路径 1（最小）：加 `synthesize_to_wav_for_remote(text)`，把 PCM 套 44 字节 WAV 头返回，前端 `new Audio(blob)` 直接吃
- **必须先实测**：在 `_stream_pcm_from_qwen` 加 `[QwenTTS] 首块到达 X ms` 埋点，对比 edge-tts 的 ~900 ms warm
- 决策：
  - qwen 首块 < 500 ms → 切 qwen 收益明确
  - qwen 首块 ≈ 800 ms → 不切，做 edge-tts 预热
  - qwen 首块 < 300 ms → 进一步做 qwen-tts 真流式 + dashscope 连接池复用 LLM client

### 5.4 P3 TTS MSE 真流式
- 收益 ~200 ms（不再等单段 mp3 完整合成才推）
- 前端要把 `audio_out.js` 的 `new Audio(blob)` 改成 MediaSource + SourceBuffer
- 风险：错误处理变复杂，Android WebView 兼容性需测

### 5.5 P3 STT 流式
- 真流式 STT 要换成 faster-whisper 的 `transcribe()` + 分块输入，或换 `whisper_streaming` 类项目
- 工作量大，收益 200–400 ms，回报率不高，**最后再做**

---

## A.6 待开发板实测项

回到开发板后第一批要测的（按 5 分钟级别可完成）：

1. **qwen-tts 首块延迟**：在 `qwen_tts_client.py:_stream_pcm_from_qwen` 加 perf_counter 埋点，跑一次合成
2. **VAD 阈值灵敏度**：把 `vad_min_silence_duration_ms` 改 300，正常对话两轮看会不会被截断
3. **edge-tts 预热效果**：在 `_play_tts_any` 远端分支前面塞一行 `await edge_tts.Communicate("。", voice).stream()` 预热，看段 0 首块从 2173 ms 降多少

P0（LLM 流式）改造前**不需要**实测准备，直接动 llm_client.py + agent.py 即可。

---

# Part B：ROS 语音控制首响应

> **目标**：从用户说"起飞"/"降落"/"看地图"等固定命令到 ROS 节点真正收到 topic 的间隔。  
> **当前基线**：~5.9 s（和场景 A 共享 STT/LLM/TTS 链路，但 LLM 那 2.3s 在这里完全是浪费）。  
> **理论极限**：~0.7 s（VAD + STT + 直接发 ROS topic，不要 TTS 反馈）。

## B.1 当前链路 & 实测耗时

```
说话 → VAD(500) → 上传(70) → STT(600) → LLM 推理(2260, 决定调 drone.takeoff)
     → MCP 执行 drone_takeoff  
     → subprocess.Popen 启动新 Python 进程（~1-1.5s 冷启动）
        ├─ Python 启动 + import: ~200ms
        ├─ import rclpy: ~300-500ms
        └─ rclpy.init + Node + Publisher + topic discovery: ~500-800ms
     → publish UInt8 到 /drone_command
     → TTS 反馈"起飞指令已下达" → 平板出声
```

| # | 阶段 | 耗时 | 是否必要 |
|---|---|---|---|
| 1 | VAD | ~500 ms | 必要（信号边界） |
| 2 | 上传 | ~70 ms | 必要 |
| 3 | STT | ~600 ms | 必要（拿到"起飞"二字） |
| 4 | **LLM 推理** | **~2260 ms** | **❌ 完全浪费** |
| 5 | **subprocess + rclpy 冷启动** | **~1000-1500 ms** | **❌ 完全浪费** |
| 6 | ROS publish | ~10 ms | 必要 |
| 7 | TTS 反馈合成 | ~2400 ms | 可选（语音确认） |
| | **端到端** | **~5.9 s** | |

## B.2 各环节根因（含代码定位）

### B.2.1 LLM 在固定命令上是纯浪费 ★ 主要瓶颈
- 调用点：`src/protocols/local_agent_protocol.py:767` `await agent.run(...)`
- 问题：所有 STT 结果都送 LLM 推理。但"起飞"/"降落"/"看地图"是**固定语义、固定参数**的命令——LLM 没有在做任何有价值的推理，只是把"起飞"两个字翻译成 `drone.takeoff()` 调用
- LLM 那 2260 ms 的代价**完全没有信息增益**

### B.2.2 ROS publisher 每次冷启动 ★ 次要瓶颈
- 位置：`src/mcp/tools/robot_dispatch/tools.py:70-99` `_publish_int_fire_and_forget`
- 实现：每次调用 `subprocess.Popen([ROS2_PYTHON, UINT8_PUBLISHER_SCRIPT, ...])`
- 问题：每次起飞都重新 fork 一个 Python 进程，重新 `import rclpy`，重新 `rclpy.init() + create_node + create_publisher + topic discovery`
- 主进程其实**已经在跑 ROS2 环境**（SLAM bridge 用 rclpy，见 `src/display/slam_bridge.py` 和 `src/utils/ros2_env.py`）——只是 publisher 没复用

### B.2.3 工具描述里的关键词其实可以反向利用
- `src/mcp/tools/robot_dispatch/manager.py:9` 里已经写了 `"开始起飞"｜"起飞"｜"系统启动"｜"执行任务"｜"出发"`——这些是给 LLM 看的提示词，**但同样的关键词集合可以直接给意图前置匹配器用**，免重写

## B.3 优化方案矩阵

| 优先级 | 方案 | 端到端预期 | 净收益 | 工作量 | 风险 |
|---|---|---|---|---|---|
| **P0** | 关键词意图直达（跳过 LLM） | 5.9 s → 1.7 s | **−4.2 s** | 中（半天-1 天） | 误触发风险（无人机） |
| **P1** | 常态化 ROS Bridge（驻留 publisher） | 1.7 s → 0.7 s | −1.0 s | 中（半天） | 主进程崩溃影响范围扩大 |
| P2 | 跳过 TTS 反馈（仅显示文字 / 极简提示音） | 进一步 −2 s | 视体验取舍 | 极小 | 用户感知不到执行 |

**P0+P1 组合：5.9 s → 0.7 s**。

## B.4 P0 详细方案：关键词意图直达 ⭐

### B.4.1 改造后的链路

```
说话 → VAD → 上传 → STT 出文本
              ↓
              意图前置匹配器（关键词 / 边界正则）
              ├─ 命中"起飞"  → 直接 await drone_takeoff()，跳过 LLM
              ├─ 命中"降落"/"返航"  → 直接 await drone_land()
              ├─ 命中"看地图"/"看建图"  → 直接 await mapping_view()
              └─ 未命中或语义模糊  → 走原 LLM 流程
              ↓
              （命中分支）
              短反馈："起飞指令已下达" → TTS（可选，或换轻量提示音）
```

### B.4.2 改造点

#### A. 新增 `src/protocols/intent_matcher.py`
```python
# 简化示例
INTENT_RULES = [
    {
        "tool": "drone_takeoff",
        "keywords": ["起飞", "出发", "执行任务", "系统启动"],
        "negation_block": ["不要", "别", "取消", "吗", "?", "？"],
    },
    {
        "tool": "drone_land",
        "keywords": ["降落", "返航", "回来"],
        "args": {"emergency": "false"},
        "negation_block": [...],
    },
    {
        "tool": "drone_land",
        "keywords": ["紧急降落", "立刻降落", "马上停"],
        "args": {"emergency": "true"},
    },
    {
        "tool": "mapping_view",
        "keywords": ["看地图", "看建图", "打开 rviz", "显示地图"],
    },
]

def match(text: str) -> Optional[tuple[str, dict]]:
    """返回 (tool_name, args) 或 None"""
    for rule in INTENT_RULES:
        if any(neg in text for neg in rule.get("negation_block", [])):
            continue
        if any(kw in text for kw in rule["keywords"]):
            return rule["tool"], rule.get("args", {})
    return None
```

#### B. `local_agent_protocol.py` 在 `_run_agent_pipeline_after_stt` 入口加 fast path
```python
async def _run_agent_pipeline_after_stt(self, user_text: str) -> None:
    if not user_text:
        ...
        return
    self._fire_json({"type": "stt", "state": "stop", "text": user_text})
    
    # ── 意图前置匹配（fast path）──
    intent = intent_matcher.match(user_text)
    if intent is not None:
        tool_name, args = intent
        self._fire_json({"type": "tts", "state": "start"})
        result = await self._get_mcp_server().execute_tool(tool_name, args)
        # 异步把这次交互补回 conversation_history，保持上下文连续性
        asyncio.create_task(
            self._get_agent().remember_exchange(user_text, result)
        )
        await self._play_tts_any(result)
        self._fire_json({"type": "tts", "state": "stop"})
        return
    
    # ── 未命中 → 走 LLM 流程（现有逻辑）──
    ...
```

`agent.py:330` `remember_exchange` 已经存在，正好用来异步补上下文。

### B.4.3 风险与缓解

| 风险 | 缓解 |
|---|---|
| "我不要起飞" 误触发 | `negation_block` 字典预先排除否定词、疑问词 |
| "起飞机准备好了吗" 误触发 | 边界匹配：要求关键词前后是字符串边界或标点；或要求 STT 文本长度 ≤ 6 字（短命令优先） |
| 关键词列表不完备 | 第一版从 `manager.py` 工具描述里抠关键词；后续从误判日志里加 |
| 误触发后无法撤回（无人机起飞） | 高风险命令保留双段触发：第一次命中只播 TTS "请确认起飞"；2 秒内再说"确认"才真发 ROS topic。**但这就回到 LLM 速度水平** |
| LLM 上下文断了 | `agent.remember_exchange` 异步追加；后续对话仍能引用"刚才你让无人机起飞了" |

### B.4.4 设计取舍

> 是否值得为安全引入二次确认？

- **当前 fire-and-forget 模式下，单次直达更符合体验**（你都已经按住说"起飞"了，再让你确认一遍很奇怪）
- **真正的安全网应该在硬件侧**（飞控自己有 RC override / 失联自动降落）
- 软件这边可以加一个**冷却期**：1 秒内只允许触发一次同种命令，防止 STT 重复识别造成连发
- 起飞这种最危险的，可以加配置开关 `INTENT_CONFIRM_TAKEOFF=true` 让有需要的场景启用二次确认

## B.5 P1 详细方案：常态化 ROS Bridge

### B.5.1 改造点

#### A. 新增主进程驻留的 ROS publisher 单例
```python
# src/utils/ros_bridge.py（或扩展现有 ros2_env.py）
class RosBridge:
    _instance = None
    def __init__(self):
        import rclpy
        from rclpy.node import Node
        if not rclpy.ok():
            rclpy.init()
        self._node = Node("aiagent_ros_bridge")
        self._publishers = {}  # topic → Publisher

    def publish_uint8(self, topic: str, value: int):
        from std_msgs.msg import UInt8
        if topic not in self._publishers:
            self._publishers[topic] = self._node.create_publisher(UInt8, topic, 10)
        msg = UInt8(); msg.data = value
        self._publishers[topic].publish(msg)
```

#### B. `tools.py:_publish_int_fire_and_forget` 改成调单例
```python
def _publish_int_fire_and_forget(topic: str, value: int) -> tuple[str, str]:
    from src.utils.ros_bridge import get_ros_bridge
    bridge = get_ros_bridge()
    # 持续发 30s 改成异步循环 + 单例 publisher
    asyncio.create_task(_repeat_publish(bridge, topic, value, duration=30))
    return "dispatched", "via ros_bridge singleton"
```

### B.5.2 注意事项

- 主进程已经初始化过 rclpy（SLAM 用），需要确认是否已有 Node 在 spin。如果有，复用其 executor；如果没有，启动一个 SingleThreadedExecutor 跑在独立线程
- 老的 `UINT8_PUBLISHER_SCRIPT` 可以保留作为兜底，跑不起来的极端情况下 fallback 到 subprocess
- topic 第一次 publish 还有 ROS2 自身的 discovery 延迟（~100ms），但一旦发现订阅者后续都是 <10ms

## B.6 待开发板实测项（场景 B 专用）

1. **subprocess 冷启动实测**：在 `_publish_int_fire_and_forget` 入口和首次 publish 之间打时间戳，确认 1-1.5s 估算是否准确
2. **意图匹配误触发率**：跑 50 条日常对话 STT 文本过一遍 `intent_matcher.match`，看误命中率
3. **rclpy 多节点共存**：确认 SLAM bridge 的 rclpy 节点和新增的 RosBridge 节点能在同一进程共存

---

# 共用：决策记录区（Decision Log）

> 每次讨论确定/否定了什么方案，写在这里。新方案补到对应场景的方案矩阵。

- 2026-04-28：确认 LLM 当前完全非流式（`stream=False` 默认），场景 A 首响应 ~5.9 s，瓶颈是 LLM 2.3 s + TTS 冷启动 2.4 s 串行。决定场景 A 的 P0 = LLM 流式 + 句级 TTS。
- 2026-04-28：第二次 chat/completions 调用归因为 `_refresh_memory_summary` 后台任务，**确认不影响首响应**，结案。
- 2026-04-28：qwen-tts 客户端只产出 Opus 帧不产出 mp3/wav，**不能即插即用替换 edge-tts 远端路径**，需先加 WAV 输出方法 + 实测首块延迟才能决策。
- 2026-04-28：句级 TTS 切分按强标点（`。！？；～\n`）切，逗号不切——首句通常 5-15 字内有强标点，逗号收益 <100ms 不值得引入韵律损失。
- 2026-04-28：识别出场景 B（ROS 语音控制）独立于场景 A，主要瓶颈是 LLM 那 2.3s 完全是浪费 + ROS publisher 每次 subprocess 冷启动 ~1-1.5s。决定场景 B 的 P0 = 关键词意图直达，P1 = 常态化 ROS Bridge。
- 2026-04-28：场景 B 不引入二次确认作为默认行为（影响体验），高风险命令（起飞）通过 config 开关 `INTENT_CONFIRM_TAKEOFF` 选择性启用；硬件侧（飞控）应有 RC override / 失联自动降落作为安全网。
