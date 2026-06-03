# SLAM 框选抓取任务 对接方案（可指导实现）

> **日期**: 2026-05-27
> **范围**: 终端侧「在地图上框选范围 → 取中心 xy → 下发抓取任务」的对接设计。复用禁飞区前端 + bridge 范式。
> **状态**: 方案阶段，未写代码。ROS 实发做占位框架，等队友 workspace（traj_utils）部署好再补。
> **关联**: [`slam_nofly_zone_design.md`](slam_nofly_zone_design.md)（前端框选 + bridge 范式来源）、队友仓库 `WS_DRONE_TOTAL_world_base`

---

## 一、目标

让终端能下发「去某位置抓取物体」的任务，对接队友的全局规划 + 抓取执行链路。

交互沿用禁飞区：**用户在 `/slam` 框一个矩形，后端取矩形中心 `(cx, cy)` 作为抓取目标点，发一条 `goal_type=1`（取物）的 GoalWithType 给飞机。** z 用固定常量。

---

## 二、对接链路（已读队友代码确认）

```
终端(本仓库)
   │  /goal_with_type  (traj_utils/msg/GoalWithType)
   ▼
global_pcd_planner_node          ← PCD 全局地图 + odom + cloud，跑 2D A*
   │  global_path  (traj_utils/msg/GlobalPathWithGoal)
   ▼
ego_replan_fsm                   ← 轨迹生成 + 抓取状态机
   │  electromagnet/cmd  (traj_utils/msg/ElectromagnetCmd)
   ▼
electromagnet_control_node       ← 吸取/释放
```

**终端只发"去哪抓"这一个点。** FSM 收到 `goal_type=1` 自动跑完整 5 阶段（`ego_replan_fsm.cpp:1030+`）：
`HOVER_DETECT(开相机视觉)` → `FINE_ALIGN(像素对准)` → `DESCEND(降到 ~0.23m)` → `ACTUATE(电磁铁 ON)` → `RISE(抬升)`。下降/对准/吸附都不归终端管。

> 你之前总结里的 `goal_type_converter_node.cpp` 实际是 `goal_type_panel.cpp`——一个 **RViz 面板插件**，离不开 RViz，不能当独立转换器。所以终端**必须直接发布 `/goal_with_type`**。

---

## 三、接口契约

### 队友要的 xy 是具体浮点数

`global_pcd_planner_node.cpp:431` 直接 `worldToGrid(goal.position.x, goal.position.y)`——**要的就是两个 float64（world 系，单位米）**，不是区域/名字/ID。矩形中心 `(cx, cy)` 就是这两个数。

### z 被规划器忽略 —— 不需要做"目标高度"输入

- 规划器做 A* 只用 x、y，**完全不读 goal.z**。
- 输出路径所有点的 z 被强制改成 planner 的 `planning_z` 参数（`publishPath` 第 531 行 `const double z = planning_z_`）。
- 抓取下降高度是 FSM 的 `descend_target_z_pickup`（默认 0.23m）。
- 巡航高度、抓取高度都不在终端手上。

**结论**：`GoalWithType.goal` 是 `PoseStamped`，结构上必须填 z，但填什么不影响飞行。终端给一个常量即可（取 `GRASP_GOAL_Z`，建议与队友 `planning_z` 一致），**UI 不做高度输入**。

### GoalWithType 字段（终端 → planner）`traj_utils/msg/GoalWithType`

| 字段 | 类型 | 抓取任务取值 |
|---|---|---|
| `header.frame_id` | string | `"world"` |
| `goal.pose.position.x` | float64 | 矩形中心 cx |
| `goal.pose.position.y` | float64 | 矩形中心 cy |
| `goal.pose.position.z` | float64 | 常量 `GRASP_GOAL_Z` |
| `goal.pose.orientation.w` | float64 | 1.0 |
| `goal_type` | uint8 | **1 = PICKUP** |
| `dwell_time` | float64 | 0.0 |
| `yaw_deg` | float64 | -1.0（用当前朝向） |
| `interrupt_mode` | uint8 | 默认 1（抢断，立即执行） |

发布话题 `/goal_with_type`，QoS `reliable`。

---

## 四、实现改动清单（照此改代码）

整体复用禁飞区那套：前端 slam.js 框选 + slam.html 面板 + web_server.py 端点 + 一个 bridge + web_display.py 接线。**矩形框选 UI 直接复用，区别只在：抓取后端把矩形塌缩成中心点，且只允许一个目标。**

### 4.1 常量（建议集中放 grasp_task_bridge.py 顶部）
```python
GOAL_WITH_TYPE_TOPIC = "/goal_with_type"
GRASP_GOAL_Z = 1.0            # 与队友 planning_z 对齐，仅占位，规划器会忽略
GRASP_GOAL_TYPE_PICKUP = 1
GRASP_DEFAULT_INTERRUPT_MODE = 1   # 1=抢断立即执行 / 0=排队
```

### 4.2 前端 `src/display/web_static/slam.js`
镜像现有禁飞区函数（`109` 行起的 `// ===== 禁飞区 =====` 整段是模板）：
- **独立"抓取"模式按钮**（不复用禁飞区面板，单独一套 `btn-grasp-*`），进入后复用框选交互（`setNoFlyDrawMode` / pointer 拖拽生成矩形那段）框出一个矩形。**只保留一个抓取目标**（再次框选覆盖上一个，不做 list）。
- 新增 `async function sendGraspTask()`：参考 `sendNoFlyZones`（`189` 行），`fetch("/api/grasp_task", {method:"POST", body: JSON.stringify({minX,maxX,minY,maxY, interrupt_mode}) })`。**前端可不算中心，原样传矩形，中心由后端算**（保持前后端职责和禁飞区一致）。
- 抓取目标只保留一个（不像禁飞区是 list），下发成功提示"已下发/已保存"。
- localStorage key 另起，如 `aiagent.slam.graspTask.v1`（参考 `NOFLY_STORAGE_KEY`，`134` 行）。

### 4.3 前端 `src/display/web_static/slam.html`
镜像禁飞区的按钮/面板 DOM（`btn-nofly-*`、`nofly-panel`），新增 `btn-grasp-*` / `grasp-panel`。

### 4.4 后端 `src/display/web_server.py`
镜像禁飞区端点（`get/post_noflyzone` 在 `175-210` 行，`set_nofly_zone_callback` 在 `88`，状态字段 `_nofly_zones/_nofly_updated_at` 在 `64-65`）：
- 新增状态字段：`self._grasp_task: dict | None = None`、`self._grasp_updated_at = 0.0`、`self._grasp_task_callback = None`。
- 新增 `set_grasp_task_callback(self, callback)`：签名 `async (payload: dict) -> dict | None`，对齐 `set_nofly_zone_callback`。
- 新增 `_validate_grasp_task(payload)`：校验 `minX/maxX/minY/maxY` 为 float、矩形非空（参考 `_validate_nofly_zones`），**计算 `cx=(minX+maxX)/2, cy=(minY+maxY)/2`**，归一化出：
  ```python
  {"cx": cx, "cy": cy, "z": GRASP_GOAL_Z,
   "goal_type": GRASP_GOAL_TYPE_PICKUP,
   "interrupt_mode": int(payload.get("interrupt_mode", GRASP_DEFAULT_INTERRUPT_MODE)),
   "frame_id": "world", "source": "slam_web", "updated_at": time.time()}
  ```
- 新增 `GET /api/grasp_task`：返回最近一次任务 + `ros_publish_configured = self._grasp_task_callback is not None`（参考 `get_noflyzone`）。
- 新增 `POST /api/grasp_task`：解析→校验→存 `self._grasp_task`→若有 callback 则 `await callback(payload)`，返回 `publish` 结果（结构完全照 `post_noflyzone`）。

### 4.5 新文件 `src/ros/grasp_task_bridge.py`
**直接照 `src/ros/nofly_zone_bridge.py` 的占位范式写**（不是 drone_command_bridge 的实发范式）：
- `class GraspTaskBridge`，`available` 属性，`start()` / `stop()` / `submit(payload)` / `latest_task()`。
- `start()`：**当前 `self._available = False`，不创建 ROS publisher**（traj_utils 未确认前不实发，与禁飞区同策略），打日志说明 "publish disabled until traj_utils confirmed"。
- `submit(payload)`：存 `self._latest_task = payload`，打日志，返回 `{"configured": True, "published": False, "topic": GOAL_WITH_TYPE_TOPIC, "reason": "traj_utils_pending"}`。
- 单例 `get_grasp_task_bridge()`（照 `get_nofly_zone_bridge`）。
- **P2 接 ROS 时**在 `start()` 里：`import rclpy` + `from traj_utils.msg import GoalWithType`，建 Node + publisher（参考 `drone_command_bridge.py` 的 executor/线程结构），`submit` 里组装 GoalWithType（按 §三字段表，`goal.pose.position.x=cx, y=cy, z=GRASP_GOAL_Z`）并 publish。

### 4.6 接线 `src/display/web_display.py`
镜像禁飞区接线（`14` import、`25` 构造、`42` set callback、`107` start）：
- `from src.ros.grasp_task_bridge import get_grasp_task_bridge`
- `self.grasp_bridge = get_grasp_task_bridge()`
- `self.server.set_grasp_task_callback(self.grasp_bridge.submit)`
- `await self.grasp_bridge.start()`（放在 `nofly_bridge.start()` 附近）

---

## 五、PCD 更换（⏸ 等实机，本期不做）

> 本期只标注换图方法，**不实际换图、不标定**，等实机有真实地图和 odom 后再做（归入 P3）。下面是届时的操作要点。

- 换图**只改 launch 参数**，不动 cpp：`global_pcd_planner.launch.py:26` 的 `pcd_path`。
- 源码默认值（`global_pcd_planner_node.cpp:34` = `/home/intelcup/...`）与 launch 默认值（`/home/isaac/...`）不一致，**以 launch 为准**（参数覆盖源码）。
- 换图后复核与地图尺度匹配的参数：`resolution`、`inflation_radius`、`planning_z`、`obstacle_min_z/max_z`、`map_margin`。
- `world_frame` 必须与你们 SLAM/odom 的 frame 一致；框选中心 xy 必须落在新 PCD 的 world 范围内，否则 planner 报 "outside map bounds"。

---

## 六、待确认（要队友 / 实机定）

1. odom 实际话题名与 frame 是否为 `drone_0_Odometry_world` / `world`。
2. 实时点云话题、frame 是否一致。
3. 你们 PCD 地图路径与坐标原点。
4. `GRASP_GOAL_Z` 取值（与队友 `planning_z` 对齐）。
5. 默认 `interrupt_mode`：单次抓取建议 1（抢断）。

## 七、本期明确不做

- 不实际创建 ROS publisher、不实发 `/goal_with_type`（等 traj_utils 部署确认，P2 再开）。
- 不做坐标转换/视觉定位（队友 FSM 已有视觉对准，终端只给中心 xy）。
- UI 不做"目标高度"输入（z 是常量、被规划器忽略）。
- 不做任务序列编排（取物→放物→降落多航点），本期单点抓取。
- 不改队友任何 cpp。

---

## 八、分阶段

- **P1**：前端框选抓取 + 后端 `/api/grasp_task` + `grasp_task_bridge.py` 占位（不 publish）。整条链路前后端打通，bridge `available=False`。
- **P2**：队友 workspace 部署、`import traj_utils.msg` 验证通过后，bridge `start()` 接 rclpy 实发 `/goal_with_type`，联调抓取闭环。
- **P3**（可选）：LLM 关键词触发下发（按 [[project_tier_architecture]] 扩 Tier 0 `INTENT_KEYWORDS`）；PCD 换图与参数标定。
