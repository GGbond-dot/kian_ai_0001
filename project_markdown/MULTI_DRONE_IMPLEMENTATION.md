# 多无人机配送 — 实现说明

> 本文档记录终端侧多机配送的**实际实现**。队友的 `MULTI_DRONE_PLANNING_DESIGN.md`
> 提出的是"两架独立对等机、各跑各的单循环、被动绕路防撞、不新增总控工具"模型，
> 与真实业务场景（协同编排 + 多次往返）不符。本实现以真实场景为准，在其基础上
> **新增了编排器和多循环任务**（文档故意没做的部分）。

## 1. 目标业务流程

```
① 用户在 /slam 画抓取框 → 说"货到了配送"(或按钮)
② 默认机(a0)起飞 → 飞到抓取区 → 停稳约1秒 → 语音播报"识别到3个货物" → 抓第1个
③ a0 离开抓取框 → 触发空闲机(b1)起飞接力(先后起飞防撞) → b1 去抓第2个
④ a0 配送完 → 不落地，回抓取区抓第3个(多循环)
⑤ 抓取区取空 → 各机返航降落 → 空闲
⑥ 用户画新抓取框 → 说"空闲的飞机去配送" → 空闲机自动去新区
```

约定（与用户确认）：
- 默认机 = a0(0号)。手动起飞/降落/悬停**必须点名**，未点名 AI 反问哪一架。
- 货物数**写死 = 3**，到区直接播报"识别到3个货物"（不接真实视觉计数）。
- 放物点来源：抓到货后扫 QR 解码得到的坐标（沿用现有视觉流程）。
- 抓取点 = 框内固定点（框中心），多循环回去抓也是飞回该点。
- "离框" = odom 飞出所画框即算（无缓冲），立即触发下一架。
- 飞控沿规划 path 飞（防冲突有意义）。每架一个 goal_topic（/a/、/b/）。
- 起飞前置校验：没画抓取框就喊配送 → AI 拒绝并提醒，不起飞。

## 2. 配置

`config.json` / `config.example.json`：

```json
"DRONES": [
  {"key":"a0","label":"一号机","namespace":"a","drone_id":"0","command_topic":"/a/drone_command","goal_topic":"/a/goal_with_type","enabled":true},
  {"key":"b1","label":"二号机","namespace":"b","drone_id":"1","command_topic":"/b/drone_command","goal_topic":"/b/goal_with_type","enabled":true}
],
"MULTI_DRONE": {"safety_radius":1.0,"reservation_ttl_sec":120,"default_drone_key":"a0"}
```

**单机兼容**：删掉 `DRONES`（或留空）时，从旧 `GLOBAL_PLANNER.namespace/drone_id`
生成单机配置，command_topic 回退历史的 `/drone_command`，老流程照常运行。
规划器共享参数（pcd_path/resolution/...）写在 `GLOBAL_PLANNER`，各机继承、可被 DRONES 条目覆盖。

## 3. 新增文件

- `src/ros/drone_config.py` — `DroneConfig` + `load_drone_configs()`（解析 DRONES，无则单机回退）。
- `src/ros/multi_drone_coordinator.py` — **配送编排器**（odom 驱动的时序状态机）。
- `src/ros/path_reservation.py` — 多机路径预约（防撞）。

## 4. 编排器状态机（MultiDroneCoordinator）

每架机一个 `DroneTask`，phase 流转（全靠终端 odom 轮询判定，不依赖飞控反馈）：

```
IDLE →[start_delivery]→ GOTO_ZONE →[odom进框停1s]→ AT_ZONE
  →[播报"识别到3个货物"+抓取(到框点即算,扣减剩余)+读扫码放物点]→ DELIVERING
  →[odom离框→若区内剩货且有空闲机:起飞下一架接力]
  →[odom到放物点]→ 区内还剩? ─剩>0→ GOTO_ZONE(回框点,不落地,多循环)
                              └剩=0→ LANDING →[odom到家]→ IDLE(释放预约)
```

- 判定阈值：进框=点在 rect 内；离框=点出 rect；到放物点/到家=距离 < `arrive_threshold`(0.5m)。
- 抓取框范围：前端框选时已传 `minX/maxX/minY/maxY`，后端 `_validate_grasp_task` 存入
  grasp payload 的 `rect` 字段 → `goal_selection_store`，编排器直接读 `rect`，无需改前端。
- 防重复抓取：到区时若货已被别的机抓完(remaining≤0) → 直接返航降落，不抓空货。
- 未点名/"空闲的飞机"：`_pick_idle_drone()` 选空闲机（默认机优先）。
- 触发入口：MCP 工具 `drone.start_delivery(drone_key?)`；前置校验无 rect 框选 → 拒绝提醒。

## 5. 防撞预约（path_reservation）

- 每次下发路径(`_dispatch`)时，用 planner 实际规划出的路径点登记一条预约。
- 规划某机路径前，把"其他机当前位置 + 其他机预约路径点"作为外部障碍喂给 planner，
  planner 在 `_build_occupancy` 里按 `safety_radius`(1.0m) 膨胀避开。
- 降落完成主动释放；`reservation_ttl_sec`(120s) 兜底防任务卡死堵路。
- v1 只做 2D 水平预约，不做时间维度速度避碰。

## 6. planner 改动（kian_global_planner.py）

- 构造接收 `drone_key`，进 status/last_plan。
- `set_auto_land(enabled)` + `_auto_land_enabled` 门控：编排器接管时关掉 planner 自带的
  "放物到达→自动返航降落"，避免与编排器的"送完判断回区还是降落"打架（单机默认仍开）。
- `latest_odom()` 供编排器轮询位置。
- `dispatch_selected/plan_and_publish/_build_occupancy` 增加 `external_obstacles` 参数；
  `_occupancy_for_xy()` 按给定半径膨胀；`last_path_points()` 供登记预约。

## 7. 其他按 key 改动

- `ros_terminal.py`：`self.planners: dict` + **共用一个 ROS node/executor/spin 线程**；
  `planner_for(key)`、`set_path_callback` 扇出到各机、构造/启停 coordinator。
- `drone_command_bridge.py`：按 topic 的注册表（默认 `/drone_command` 兼容）；
  web_display 按各机 command_topic 建并启停 bridge。
- `goal_selection_store.py` / `detection_store.py`：`_by_key` + 全局回退（单机不破）。
- `vision_plugin.py`：`get_detection/dispatch_place` 接 drone_key。
- MCP `tools.py`/`manager.py`：takeoff/land/hover/dispatch/planner_status/vision 加 `drone_key`，
  描述写明 a0=一号机/b1=二号机、起降未点名应反问。新增 `drone.start_delivery`。

## 8. 前端（P7，低风险部分）

- `web_server.py`：新增只读 `GET /api/drones`、`GET /api/drones/status`（含编排器状态）。
- 规划路径按机分 channel：默认机走 `CHAN_PATH(0x04)` 不变，第 i 架走 `0x40+i`，
  老前端遇未知 channel 自动忽略（不会崩）。`broadcast_planned_path` 加 `drone_key`。
- `slam.js/html/css`：每机路径线不同颜色 + 多机状态图例（轮询 JSON，不碰二进制协议）。

## 9. 未完成 / 待确认（需运行机 + 队友）

- **运行机验证**：本机 jazzy 仅开发不编译，整套 ROS 行为需在 humble 运行机实跑验证。
  已做：全文件 `py_compile`、`load_drone_configs` 单机/多机、store 按key、编排器状态机
  仿真（3货多循环 goal_type 序列 [1,2,1,2,1,2,3]）、双机接力、预约避障 —— 均脱 ROS 通过。
- **二号机相机**：vision 目前仍单相机单 node。二号机扫码识别需要二号机相机 topic
  （待问队友，如 `/b/camera/...`），以及 vision 多 node 订阅 + 视频窗口切换按钮。
- **抓取框按机绑定**：grasp_task_bridge 目前全局提交（前端尚未传 drone_key）。
- **per-drone scan/odom 3D 图层**：未做（涉及二进制协议扩展，风险高，留真机迭代）。
- **播报文案**：每次进框都播报"识别到3个货物"（写死3），复访也报3，可后续按剩余调整。
- **语音路由**：已确认"起飞"=配送(无单独起飞)。INTENT_KEYWORDS 的"起飞/起飞配送/货到了
  配送/开始配送"等已改路由到 `drone.start_delivery`(不再走 drone.takeoff);系统提示词也已
  说明起飞即配送、起飞前必须先框选抓取区。drone.takeoff/land/hover 退为底层手动指令。
  **注意:此改动在 config.example.json,运行机需把 config.json 的 INTENT_KEYWORDS 与
  system_prompt 同步更新。**
