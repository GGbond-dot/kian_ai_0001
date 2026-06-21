# 摄像头推流开关（`/a/camera/enable`）接入设计

> **日期**: 2026-06-14
> **状态**: 已按本文实现，待开发板实机验证
> **关联**: [`three_tier_router_design.md`](three_tier_router_design.md)（Tier 0 语音直达）、
> `vision_and_auto_landing_design.md`（视觉链路）、
> [`slam_base_map_and_nfz_design.md`](slam_base_map_and_nfz_design.md)（同批改了禁飞区 z 过滤）

---

## 一、目标与背景

无人机端有一个相机推流总开关 service：

```bash
ros2 service call /a/camera/enable std_srvs/srv/SetBool "{data: true}"
```

调它（`data: true`）相机才开始往 topic 推图，VisionBridge 才收得到帧。
本期把这个开关接进终端，让操作员能**手动**控制推流的开/关。

### 为什么是手动开关，而不是常开

需求：**平时巡航不开，飞到货物附近再开**，免得 YOLO 一直跑挤占 CPU。

关键机制（理解整套设计的前提）：

- **VISION 插件（检测循环）一直常驻**，`VISION.enabled=true` 时随主程序启动，**不随开关启停**。
- 循环是帧驱动的：`pop_latest_frame()` 拿不到帧就 `sleep(0.005)` 空转，**只有拿到帧才跑 YOLO**。
- 所以「相机推流开关」本质 = **CPU 负载开关**：
  - 关 → 相机不推流 → 循环空转 → YOLO 不跑 → CPU≈0
  - 开 → 有帧 → YOLO 干活 → 吃 CPU/NPU
- YOLO 模型在插件 setup 时就加载好，开关只控推流，**没有「现开现加载」的延迟**，到货物附近一开即用。

> 比喻：插件=一直在岗的工人，相机推流=传送带，YOLO检测=检查包裹这个动作。
> 按钮关的是传送带（工人发呆不费力），不是让工人下班。

### 一个边界（重要）

整条链路**依赖 `VISION.enabled=true`**：视觉没启用就没有 ROS node、没有 service client，
前端按钮和语音都无东西可调。这不是强加限制——前端浮窗的画面本来就只由 VisionPlugin 推
（`broadcast_video_frame`），VISION 关着浮窗本就是空占位。「不开 YOLO 只看原始画面」需另写
独立裸视频通道，本期不做。

---

## 二、三个入口，同一条底层链路

前端按钮 / 语音 Tier 0 / LLM Tier 2 工具，最终都汇聚到同一个方法，无重复实现：

```
前端浮窗按钮 ─┐
语音 Tier 0  ─┼─→ VisionPlugin.set_camera_stream(enable)
LLM 工具调用 ─┘        └→ VisionBridge.set_camera_enable(enable)
                              └→ /a/camera/enable  (std_srvs/SetBool)
```

---

## 三、改动清单

### 3.1 `src/ros/vision_bridge.py` — service client（核心）

- `attach_ros` 里 `node.create_client(SetBool, "camera/enable")`。
  **用相对名**，靠 node 的 `namespace="a"` 自动拼成 `/a/camera/enable`（与相机 topic 同款命名空间处理，不写死 `/a/`）。
- 新增 `set_camera_enable(enable, timeout=2.0) -> bool`：`wait_for_service` + `call_async`。
- ⚠️ **死锁坑**：该 node 已被 `SingleThreadedExecutor` 在独立线程 `spin()`，
  **不能**用 `spin_until_future_complete`（与后台 executor 抢同一 node 死锁）。
  改用 `future.add_done_callback` + `threading.Event` 等待，让后台 executor 线程去完成 future。
- `detach()` 里 `destroy_client`。

### 3.2 `src/plugins/vision_plugin.py`

- `async set_camera_stream(enable) -> bool`：`VISION` 未启用抛 RuntimeError；
  阻塞的 service 调用用 `asyncio.to_thread` 丢线程池，不卡事件循环。

### 3.3 MCP 工具 `vision.set_camera`

- `src/mcp/tools/robot_dispatch/tools.py`：`vision_set_camera(args)`，`enable` 参数，
  返回「已打开/关闭摄像头推流」或失败原因。
- `manager.py`：注册 `vision.set_camera`，`PropertyType.BOOLEAN` 的 `enable`（默认 True）。

### 3.4 后端 HTTP 端点（前端按钮走这条，**不走 WS**）

- `src/display/web_server.py`：`POST /api/camera_enable`（body `{enable: bool}`）+
  `set_camera_enable_callback` setter + `_camera_enable_callback` 字段，照禁飞区端点范式。
- `src/display/web_display.py`：`_set_camera_enable(enable)` 接到 vision 插件，setup 里注册 callback。

> **为什么用 HTTP 而非 WS 指令**：video.js 在主页和 `/slam` 共用，而 **`/slam` 页面没有主 `/ws` 连接**
> （slam.js 只连 `/ws/slam`，禁飞区/抓取都走 `fetch`）。HTTP 两端统一、无常驻连接，与现有
> `sendNoFlyZones`/`sendGraspTask` 范式一致。

### 3.5 前端 `video.js` / `video.css` / `index.html` / `slam.html`

- video.js 加 `setCamera(enable)` → `fetch POST /api/camera_enable`。
- 联动语义：**开浮窗=开推流，关浮窗（×或再点）=关推流**。
  注意 `setCamera` 只在用户主动点击时发，不在 `onmessage` 收帧的 `show()` 里发（防循环）。
- 按钮缩小：`.video-toggle-btn` 改成 38px 圆形纯图标钮（文案 `📹 视频`→`📹`）。
- 缓存版本 bump：`video.js?v=2→3`、`video.css?v=3→4`。

### 3.6 Tier 0 语音（`config.json` 的 `INTENT_KEYWORDS`）

同一工具 `vision.set_camera` 要配「开/关」两组不同 args，但 `INTENT_KEYWORDS` 用工具名作 key（唯一）。
**最小增强**：`intent_matcher.py` 的 dict spec 支持可选 `"tool"` 字段覆盖 key 作真正工具名（向后兼容）。

```json
"camera_on":  { "keywords": ["打开摄像头","开启视频","开摄像头","打开视频","开启摄像头"],
                "tool": "vision.set_camera", "ack": "", "args": {"enable": true} },
"camera_off": { "keywords": ["关闭摄像头","关掉摄像头","关视频","关闭视频","关摄像头"],
                "tool": "vision.set_camera", "ack": "", "args": {"enable": false} }
```

`ack` 留空 = **查询型**：等 service 结果再播报，成功/失败原因（如「相机服务无响应」）都会被念出来。

---

## 四、待办（开发板验证）

- [ ] 开发板 `config.json` 同步 `INTENT_KEYWORDS.camera_on/off`，并确认 `VISION.enabled=true`。
- [ ] 实机调 `/a/camera/enable`：service 未上线时应超时返回失败、语音播报「相机服务无响应」、不崩。
- [ ] 主页与 `/slam` 两端按钮都能开/关推流；按钮缩小后不挡发送按钮（已留底部偏移）。
- [ ] 语音「打开/关闭摄像头」走 Tier 0 命中、真调 service。
- [ ] 压测确认：关推流时 CPU 回落、开推流时 YOLO 正常吃 NPU。
