# 物流系统终端机器人 — 场景 B(ROS 语音控制)bridge 化(接力交接 v5,**已落地软件层**)

> 接 `background03.md`(v4 已落地:平板直连 TTS + 双预热 + qwen3-asr-flash STT,端到端 ~1.23s)。
> v5 完成:drone publisher 从 `subprocess.Popen` 每次冷启动改为主进程驻留 Node 单例;新增 `drone.hover` 工具(value=3);"停止/停下" 归 land;`mapping.view` 改纯文本反馈;ROS env 固定到 main.py。
>
> **状态**:软件层全部已落地,日志路径全部跑通(Tier 0 命中 + bridge.publish_command 调度 + ack TTS 推送平板)。**飞控端是否真收到 UInt8 帧未现场验证**(用户当天没去飞机面前测,留作下次 syncpi 后实测)。

---

## ★ 开发模式(沿用 background03)

- PC(Ubuntu 24.04)只写代码,不跑项目
- 通过 `syncpi` 同步到开发板(Ubuntu 22.04,ARM)运行
- ROS humble 是给开发板的,不是写错
- PC 上允许:静态语法 / JSON 校验,不允许启动项目

---

## 场景 B 是什么

参考 `FIRST_RESPONSE_LATENCY.md` Part B。简言之:

- "起飞 / 降落 / 悬停" 是固定语义命令,LLM 推理是纯浪费 → **Tier 0 关键词直达接管,跳过 LLM**
- ROS publisher 原本每次起 subprocess 跑 `scripts/ros2_int32_publisher.py`,fork + import rclpy + create_publisher + topic discovery 估算 1–1.5s 冷启动 → **本轮改为主进程驻留 Node 单例,与 SlamBridge 平行共存**

---

## v5 已落地清单

### 1. DroneCommandBridge(常驻 publisher 单例)

**文件**:`src/ros/drone_command_bridge.py`(新建 + 新建 `src/ros/__init__.py`)

- 主进程内独立 Node `drone_command_bridge`,`SingleThreadedExecutor` 跑独立 daemon 线程
- 与 SlamBridge 共享 `rclpy.init()`,两边都用 `if not rclpy.ok():` 防重入
- `publish_command(value, duration=8.0)`:**新命令立即覆盖旧命令**(决策 Q1)—— cancel 旧 task → 立即 publish 一帧 → 起新 persist task,前 8s 每 100ms 发一次
- 启动时空 publish 一帧 value=0 做 topic discovery 预热
- 启动失败(rclpy 不可用 / Node 创建失败)时 `available=False`,调用方走 fallback
- 单例:`get_drone_command_bridge()`

### 2. tools.py 改造

**文件**:`src/mcp/tools/robot_dispatch/tools.py`

| 改动 | 说明 |
|---|---|
| `_publish_int_fire_and_forget` | 改 async,优先走 bridge,bridge 不可用回退原 `subprocess.Popen` 路径 |
| `drone_takeoff` / `drone_land` | 改成 await 异步发布;land 取消 emergency 参数,统一 value=2 |
| `drone_hover` | **新增**,value=3,语义"原地保持高度" |
| `mapping_view` | 改成纯文本 `return "地图正在实时更新，直接看屏幕就好"`,不再启动 rviz2 |
| `ROS2_PUBLISH_DURATION_SEC` | 默认 30s → 8s(决策 Q2) |
| `CMD_EMERGENCY_LAND` | 删除,value=3 重新分配给 hover |
| `RVIZ_BIN` / `RVIZ_CONFIG_PATH` / `_rviz_already_running` | 删除,mapping.view 不再起进程 |

### 3. manager.py 工具注册

**文件**:`src/mcp/tools/robot_dispatch/manager.py`

- 新注册 `drone.hover`,工具描述写清"原地悬停,保持当前高度"
- `drone.land` 描述去 emergency 参数,关键词列表加"停止/停下/回来"
- `mapping.view` 描述说明"建图已自动推送到平板,本工具仅返回提示文案,不启动外部进程"

### 4. INTENT_KEYWORDS 增补

**文件**:`config/config.json`

```json
"INTENT_KEYWORDS": {
  "drone.takeoff": { "keywords": ["起飞", "升空", "飞起来", "开始启动", "执行任务", "出发"], "ack": "好的，正在起飞" },
  "drone.hover":   { "keywords": ["悬停"],                                                  "ack": "好的，原地悬停" },
  "drone.land":    { "keywords": ["降落", "返航", "落地", "下来吧", "回来", "停止", "停下"], "ack": "收到，开始降落" },
  "mapping.view":  { "keywords": ["看地图", "看建图", "显示地图", "打开地图"],               "ack": "地图正在实时更新，直接看屏幕就好" },
  "drone.status":  { "keywords": ["电量", "状态", "还能飞", "续航"],                         "ack": "" }
}
```

注意 `drone.hover` 必须排在 `drone.land` 前面 —— intent_matcher 按 dict 顺序遍历,虽然当前关键词集合无碰撞,但显式顺序更安全。

### 5. web_display.py 启动 bridge

**文件**:`src/display/web_display.py`

- `__init__` 里 `self.drone_bridge = get_drone_command_bridge()`
- `start()` 里 `await self.slam_bridge.start()` 之后追加 `await self.drone_bridge.start()`
- `close()` 里先停 drone_bridge 再停 slam_bridge(销毁顺序)

### 6. slam_bridge.py 防 rclpy 重复 init

**文件**:`src/display/slam_bridge.py:192-193`

原本无脑 `rclpy.init(args=None)`。现在:
```python
if not rclpy.ok():
    rclpy.init(args=None)
```
原因:drone_bridge 在 `web_display.start()` 里同步先 init,后续 slam 的 `_run_ros` 任务再跑就会撞 "already init" 错误。

### 7. ROS env 固定到 main.py

**文件**:`main.py`

```python
import os
os.environ.setdefault("ROS_DOMAIN_ID", "10")
os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
```

放在所有 `from src...` import 之前,保证 rclpy 起的时候已经看见正确的 DDS 配置。
用 `setdefault` 而不是 `=` —— 保留命令行 `export` 覆盖能力,以后调试不同 DOMAIN_ID 仍然生效。

---

## 协议(value 码表)

| value | 含义 | INTENT_KEYWORDS 命中(直达) |
|---|---|---|
| 1 | 起飞 | 起飞、升空、飞起来、开始启动、执行任务、出发 |
| 2 | 降落 / 停止 | 降落、返航、落地、下来吧、回来、**停止**、**停下** |
| 3 | 悬停 | 悬停 |

- topic:`/drone_command`(UInt8)
- publish 周期:100ms,持续 8s(80 帧),新命令 cancel 旧任务立即覆盖

---

## 实测日志摘录(2026-05-02 20:25)

### 软件链路全跑通

```
20:25:14,114 STT 结果：'飞起来吧。'
20:25:14,117 [Tier2] 触发原因=tier2-fallback-or-default     ← 当时 fast_path_enabled=false 测兜底
...
20:24:52,237 DroneCommandBridge: 启动成功 topic=/drone_command   ← bridge 启动 OK
```

打开 fast_path 时:
```
20:17:34,333 [Tier0] 命中关键词 '起飞' → 工具 drone.takeoff ack='好的，正在起飞'
20:17:34,340 [TTS/direct] seg=0 ok 字数=7 推送=1ms 客户端=2
20:17:34,423 [Agent] 执行工具：drone.takeoff，参数：{}
20:17:34,423 [无人机] 发送起飞指令 UInt8=1
20:17:34,426 [Tier0] 工具 drone.takeoff 完成: ...执行成功
```

### 端到端首响应时延(场景 B)

| 阶段 | 时延 |
|---|---|
| STT 完成 → Tier0 命中 → ack TTS 推送平板 | **571ms**(从 20:17:50,069 到 20:17:50,640)|
| Tier0 命中 → bridge.publish_command 调度 | <10ms |

**端到端目标 <700ms 达成**(改造前估算 5.9s,因为带了 LLM 2.3s + subprocess 冷启动 ~1-1.5s)。

**未实测**:
- 飞控真收到 UInt8 帧的延迟(没在飞机面前测,需 `ros2 topic echo /drone_command` 验证 bridge 真发出去了)
- 改造前 subprocess 冷启动具体耗时(没量基线)

---

## 已敲定的决策(v5 总集)

| # | 决策 | 出处 |
|---|---|---|
| 1 | bridge 解耦:`src/ros/drone_command_bridge.py`,独立 Node + 独立 executor 线程,共享 `rclpy.init()` | Q1 |
| 2 | publish 持续时长 30s → 8s | Q2 |
| 3 | `mapping.view` 改纯文本"地图正在实时更新，直接看屏幕就好",不调 ROS | Q3 |
| 4 | "停止 / 停下" 归 `drone.land`(value=2);"悬停" 是新 `drone.hover`(value=3) | Q4 |
| 5 | bridge 启动时机:`web_display.start()` 时拉起,跟随程序生命周期 | Q2-启动 |
| 6 | 新命令直接 cancel 旧 task,不排队 | Q1-覆盖 |
| 7 | bridge 启动时空 publish 一次预热 topic discovery | Q2-启动 |
| 8 | 保留 `scripts/ros2_int32_publisher.py` 作 bridge 不可用时 fallback,不删 | 风险缓解 |
| 9 | drone 工具的覆盖率靠扩 `INTENT_KEYWORDS`,**不靠改 LLM Tier 2 system_prompt** | 实测后追加 |
| 10 | ROS env 固定到 main.py 用 `os.environ.setdefault`,保留命令行 export 覆盖能力 | 实测后追加 |

---

## 实测踩坑(v5 新增)

### 坑 1:Tier 2 LLM 兜底"嘴上说调工具,其实没调"

**现象**:`config.ROUTER.fast_path_enabled: false` 关掉 Tier 0 后,说"飞起来吧"→ 走 Tier 2 → LLM 回复"已执行起飞指令。" → **但日志没有 `[Agent] 执行工具：drone.takeoff`**,飞机不会动。

**原因**:`config.LLM.system_prompt` 里只列了"开始起飞/系统启动/执行任务",没列"飞起来"。LLM 觉得不在白名单,直接回复文本糊弄。

**决策**:**不修**。Tier 2 原本是给日志分析 / 高级决策用的,不应该承担 drone 工具兜底职责。drone 工具的覆盖率靠扩 `INTENT_KEYWORDS` 关键词解决。

**给后续的人**:不要为了让 Tier 2 LLM 调 drone 工具而改严 `LLM.system_prompt`,会污染 Tier 2 的本职定位。要新增起飞口语化表达,就加进 `INTENT_KEYWORDS["drone.takeoff"]["keywords"]`。

### 坑 2:fast_path 优先级最高,关键词命中就抢工具,不走 LLM

**现象**:用户想测 LLM 兜底,直接说"飞起来吧",日志显示 `[Tier0] 命中关键词 '飞起来' → 工具 drone.takeoff`,根本没经过 LLM。

**原因**:这是预期行为,不是 bug。Tier 0 设计上就是优先级最高,只要命中关键词,直接调 MCP 工具,跳过 LLM 路由。

**测 LLM 兜底的正确姿势**:`config.ROUTER.fast_path_enabled: false` 改成 false 后重启,再说话。测完记得改回 `true`,不然 Tier 0 优化就没了。

### 坑 3:rclpy.init 重复调用崩溃

**现象**(理论,改 slam_bridge 之前):`drone_bridge.start()` 同步先调 `rclpy.init()`,稍后 slam 的 `_run_ros` 任务跑起来又调一次 → `RuntimeError: rclpy.init() already called`。

**已修**:slam_bridge.py:192 加 `if not rclpy.ok():` 检查;drone_bridge 同样的模式。两边幂等,谁先到谁 init。

**给后续的人**:本进程内再加 ROS Node 时,**永远** `if not rclpy.ok(): rclpy.init()`,不要无脑 init。

### 坑 4:同步 MCP 工具调异步 bridge

`drone_takeoff` / `drone_land` / `drone_hover` 是 `async def`,所以可以直接 `await _publish_int_fire_and_forget(...)`,后者也是 `async`,内部直接 `await bridge.publish_command(...)`。**没有用 `asyncio.run_coroutine_threadsafe`**,因为整条链都在主 asyncio loop 里,不跨线程。

之前 background04 planning 版本提了 `run_coroutine_threadsafe`,实施时发现不需要。bridge 的 SingleThreadedExecutor 在独立线程跑,但 publisher.publish() 是线程安全的(rclpy 保证),从主线程直接调即可。

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| rclpy 多 Node 同进程冲突 | 两边都用 `if not rclpy.ok()` 防重入,各自独立 executor 线程不抢 |
| bridge 启动失败(rclpy 不可用) | `available=False`,自动 fallback 原 subprocess 路径,WARNING 级日志 |
| 8s persist 期间反复说"起飞" | bridge 内 cancel 旧 task 起新 task 是幂等的,飞控收到的 value 不变 |
| Tier 2 LLM 兜底调不到工具 | **接受**(见踩坑 1)。drone 命令唯一通道是 Tier 0 关键词直达 |
| 子串匹配边界(如"取消悬停" 误命中 hover) | 当前不处理,出现误触发再做边界正则 |

---

## 待开发板实测项(下次 syncpi 后)

按优先级:

1. **飞控真收到 UInt8 帧**:另开终端 `ros2 topic echo /drone_command`,说一次"起飞",看能否 echo 出 `data: 1` —— **本次未做**
2. **覆盖语义**:连说"起飞" → 立刻"降落",看 8s persist 任务有没有被 cancel(echo 终端看 value 是不是立即从 1 变 2)
3. **悬停**:说"悬停",`echo` 看到 `data: 3`
4. **mapping.view**:说"看地图",平板播"地图正在实时更新…",**不**起 rviz2,日志不应有 subprocess.Popen rviz 痕迹
5. **改造前后端到端对比**:把 bridge 强制置不可用 / 改 fallback 路径常开,跑 subprocess 路径,对比起飞响应时间
6. **多 Node 共存压测**:bridge 启动后,平板 /slam 页面持续看图 30s 以上,观察是否丢帧或卡顿

---

## 关键文件位置速查(场景 B 相关)

```
新建:
  src/ros/__init__.py
  src/ros/drone_command_bridge.py            ← 常驻 publisher 单例 + executor 线程

改动:
  main.py                                    ← os.environ.setdefault ROS_DOMAIN_ID/RMW
  src/mcp/tools/robot_dispatch/tools.py      ← _publish_int_fire_and_forget 走 bridge(保留 fallback)
                                               + drone_hover 新增
                                               + mapping_view 改纯文本
                                               + 30s→8s + 删 RVIZ 常量
  src/mcp/tools/robot_dispatch/manager.py    ← 注册 drone.hover + 改 land/mapping.view 描述
  config/config.json                         ← INTENT_KEYWORDS 加 hover、land 加 停止/停下
  src/display/web_display.py                 ← start() 拉起 drone_bridge
  src/display/slam_bridge.py                 ← rclpy.init 加 ok 检查防重入

复用 / 不动:
  src/protocols/intent_matcher.py            ← 已支持新 INTENT_KEYWORDS
  src/protocols/local_agent_protocol.py      ← Tier 0 fast path 已存在
  scripts/ros2_int32_publisher.py            ← 保留作 fallback,不删
  src/display/slam_bridge.py 的 _run_ros     ← 仅改了 init 检查,其他不动
```

---

## 给下一个 Claude 的话

1. **本次实施已落地软件层全部链路,但飞控端实测留给下次**。要验证飞机真起飞,先 `ros2 topic echo /drone_command` 看 bridge 发出去的帧,再去飞机面前。
2. **drone 工具的口语化覆盖靠扩 `INTENT_KEYWORDS`,不靠改 Tier 2 system_prompt**。Tier 2 是给日志分析 / 高级决策用的,定位差异(详见踩坑 1)。
3. **rclpy.init 永远先 `rclpy.ok()` 检查再 init**,别无脑 init,会跟 SlamBridge / DroneCommandBridge 撞。
4. **fast_path 优先级最高**,Tier 0 命中就直接调工具不走 LLM。要测 LLM 路径必须临时关 `ROUTER.fast_path_enabled`,测完改回 true。
5. **mapping.view 已不调 ROS 不起 rviz2**,只返回固定文案。如果未来要恢复桌面 rviz2,新增一个独立工具(如 `mapping.rviz`),不要改 mapping.view 的语义。
6. **bridge `publish_command` 的覆盖语义是 cancel 旧 task 立即起新**,不要改成排队,会让降落跟不上起飞。
7. **持续时长 8s 是为飞控漏帧容错**,不是飞控需要持续信号。如果未来飞控改成单帧触发就够,可以再缩短。
8. **当前 dashscope API key 已多次曝光**(沿用 background03 安全清单),正式部署前用户要轮换。
9. **段 id 协议契约 / `transcribe_pcm` 必需接口**(背景03)在场景 B 不涉及,但仍然适用于其他改动。
10. ROS env 用 `os.environ.setdefault` 在 main.py 顶部固定,**保留命令行 export 覆盖能力**。不要改成直接赋值。

---

## 追加 — SLAM 点云视觉调参(2026-05-03)

与场景 B 主线无关,纯 `/slam` 平板前端观感优化。**不改 SLAM、不改 bridge、不动协议**。

### 问题
- 平板看点云**稀疏**
- scan **闪烁**(5Hz 替换太快)

### 方案分析(关键决策)
- 累积无限叠加 → 人走过留鬼影,**否决**
- 纯替换 → 闪烁 + 稀疏,**就是现状**
- **方案 D 前端滑动窗口**:bridge 一帧不变,前端累积最近 N 帧叠加显示。带宽零增长,鬼影 N×(1/Hz) 秒后自动清

### 实施
| 文件 | 改动 |
|---|---|
| `src/display/web_static/slam.js` | 新增 `SCAN_WINDOW_FRAMES=25`(5Hz×5s)+ `scanFrames` ring + `updateScanWindow()` 合并所有窗口帧重建 position attribute |
| `src/display/web_static/slam.js` | map / scan 点 size 最终定 **0.07**(试过 0.06 略细、0.08 太粗) |
| `src/display/slam_constants.py` | `SLAM_MAP_VOXEL_SIZE` 0.03→0.02,`SLAM_SCAN_VOXEL_SIZE` 0.05→0.03(密度 ~3-4×) |

### 带宽估算(单机)
- scan 5Hz × ~5000 点 × 16B ≈ 3.2 Mbps,voxel 调小后 ≈ 12-15 Mbps
- 5G WiFi 路由器舒适区(<20 Mbps),对其他设备无感
- **3 机扩展时**:线性 ×3 ≈ 30-45 Mbps,2.4G 或老路由会挤占空中时间。届时把 `SLAM_SCAN_MAX_HZ` 从 5 降到 2 即可压回

### 给后续的人
1. 想再调密度:**优先动 voxel,不要再加大 size**(粗了糊)
2. 觉得拖影长了:`SCAN_WINDOW_FRAMES` 调小(15 ≈ 3s)
3. **滑动窗口在前端,bridge 完全不知道**。如果未来要改持续时长协议,别在 bridge 找,在 slam.js
4. **3 机部署时再考虑**:差分 map(只发新增体素)、map 节流降到 0.5Hz、Map voxel 加大、必要时开发板插网线
5. 本次未实测的:voxel 调小后实际带宽数(估算)、3 机场景(没现场)
