# 视觉识别 + 自动返航降落 设计/对接文档

> **范围**：终端侧「摄像头 YOLO+QR 检测 → 货物库查放货坐标 → 全局规划下发 → 飞抵后自动返航降落 → 语音播报」的完整闭环，以及前端视频显示。
> **关联**：[`slam_base_map_and_nfz_design.md`](slam_base_map_and_nfz_design.md)（禁飞区/离线地图）、[`slam_grasp_region_design.md`](slam_grasp_region_design.md)（框选下发范式）。
> **来源**：在队友分支（vision + 自动降落）基础上融合回主线，保留本机已有的分层路由、抓取健壮性、禁飞区消费。

## 一、端到端业务流程

```
无人机摄像头
  │ ROS topic /camera/image_raw/compressed (CompressedImage)
  ▼
VisionBridge (src/ros/vision_bridge.py)        订阅图像 + 发布 vision/result、dual_validator/verified
  ▼
VisionPlugin (src/plugins/vision_plugin.py, priority=18)
  ├─ VisionDetector (src/vision/detector.py)    YOLO(OpenVINO) + QR 检测
  ├─ GoodsDatabase  (src/vision/goods_database.py) QR 码 → 货物名 + 放货坐标(place_x/y/z)
  ├─ DetectionStore (src/vision/detection_store.py) 线程安全存「最新一帧检测结果」
  ├─ draw_overlay → app.broadcast_video_frame() → WebSocket /ws/video → 前端 video.js 画中画
  ▼
LLM 经 MCP 工具消费：
  • vision.get_detection   读 DetectionStore 最新结果（detected/qr_data/goods_name/place_x/y/z）
  • vision.dispatch_place  取放货坐标 → KianGlobalPlanner 规划 → 发 GoalWithType(goal_type=2=place)
  ▼
KianGlobalPlanner (src/ros/kian_global_planner.py)
  离线地图 ⊕ 实时点云 ⊕ 禁飞区 → A* → 稀疏航点路径 GlobalPathWithGoal 发给无人机
  ▼
无人机飞到放货点(goal_type=2) → _on_odom 检测到达(<0.5m) → 标记 pending landing
  ▼
RosTerminalPlugin._poll_landing (0.1s 轮询) → planner.trigger_landing
  → 发 goal_type=3 返航降落到起飞点 → _on_mission_complete
  → app.trigger_proactive_response 语音播报「放物任务已完成，已自动返航降落」
```

## 二、关键模块与职责

| 模块 | 职责 |
|---|---|
| `src/vision/detector.py` | YOLO(OpenVINO)+QR 推理，输出 `DetectResult` |
| `src/vision/goods_database.py` | 从 YAML 加载 `QR码 → 货物名 + 放货坐标` |
| `src/vision/detection_store.py` | 进程内线程安全单例，缓存最新检测结果 |
| `src/ros/vision_bridge.py` | 订阅摄像头压缩图、发布检测结果话题 |
| `src/plugins/vision_plugin.py` | 帧驱动检测主循环 + 视频帧广播 + `get_detection`/`dispatch_place` |
| `src/ros/kian_global_planner.py` | A\* 全局规划；PLACE(2) 目标监控到达；自动返航降落(goal_type=3) |
| `src/plugins/ros_terminal.py` | `_poll_landing` 后台轮询 pending landing（避免在 ROS 回调线程跑 A\*）；`_on_mission_complete` 语音播报 |
| 前端 `video.js` + `index.html#video-pip` + `style.css .video-pip` | 连 `/ws/video` 收 JPEG 帧渲染到画中画浮窗 |

## 三、ROS 话题

| 方向 | 话题 | 消息类型 |
|---|---|---|
| 订阅 | `/camera/image_raw/compressed` | `sensor_msgs/CompressedImage` |
| 发布 | `vision/result` | `drone_task_interfaces/VisionDetectResult` |
| 发布 | `dual_validator/verified` | `std_msgs/Int8` |
| 发布 | `/goal_with_type` | `drone_task_interfaces/GoalWithType` |
| 发布 | `/a/drone_0_planning/global_path` | `drone_task_interfaces/GlobalPathWithGoal` |
| 订阅 | `/a/drone_0_Odometry_world` | `nav_msgs/Odometry` |
| 订阅 | `/a/drone_0_cloud_registered_world` | `sensor_msgs/PointCloud2` |

> 禁飞区数据**不进话题**，全程终端侧消费：Web 框选 → `POST /api/noflyzone` → `NoFlyZoneBridge`（占位、不 publish）→ planner 直接读取塑造 A\* 占用栅格 + 拒发禁区目标。

## 四、配置

`config.json` 新增（默认关闭，不影响现有功能）：

```jsonc
"VISION": {
  "enabled": false,                 // 设 true 才启用视觉链路
  "camera_topic": "camera/image_raw/compressed",
  "model_path": "models/yolo_best_openvino/best.xml",
  "device": "CPU",
  "confidence_threshold": 0.3,
  "enable_qr": true,
  "goods_db_path": "config/goods_location.yaml"
}
"GLOBAL_PLANNER": { "enabled": true, "pcd_path": "maps/global_map_ds.pcd", "planning_z": 0.5, ... }
```

## 五、运行期依赖（在 humble 运行机/共同开发板上准备）

> 本机仅开发，不编译。代码经 syncpi 推到 humble 运行机后，以下依赖需在开发板上补齐：

1. `config/goods_location.yaml` —— QR 码 → 货物名 + 放货坐标 映射表
2. `models/yolo_best_openvino/best.xml`（+ `.bin`）—— YOLO OpenVINO 模型
3. `drone_task_interfaces/msg/VisionDetectResult.msg` —— 接口包补该消息后重新 `colcon build`
4. Python：`opencv-python`、`openvino`、`pyyaml`（运行机）

## 六、自动降落语义

- 仅当下发的是 **PLACE 目标（goal_type=2）** 时才进入「到达监控」。
- 到达阈值 `completion_threshold`（默认 0.5m，可配）。
- 降落 = 发 `goal_type=3` 规划返航到**首帧 odom 记录的起飞点**，再触发完成回调播报。
- `_landing_triggered` 保证只触发一次。
