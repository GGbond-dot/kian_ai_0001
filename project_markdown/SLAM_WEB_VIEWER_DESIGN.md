# SLAM 建图 Web 可视化 — 框架设计文档

> 目标：在平板浏览器实时查看 DK2500 上 FAST-LIO 的建图效果，复用现有 FastAPI + WebSocket 框架。
> 状态：**草案，待用户确认后进入实现阶段**

---

## 0. 待确认的关键决策（写代码前必须定）

| # | 决策点 | 候选 | 默认推荐 |
|---|--------|------|----------|
| Q1 | SLAM 视图与现有 Console 的关系 | A. 新增独立页面 `/slam`<br>B. 同页面用 Tab 切换<br>C. 完全独立的二级应用 | **A** — 路径独立，互不干扰；现有 console 一行代码不动 |
| Q2 | WebSocket 通道复用方式 | A. 复用现有 `/ws`，加消息类型字段<br>B. 新开 `/ws/slam` 专用通道 | **B** — 二进制点云流量大，与控制信令混在一起会互相阻塞；分通道也方便日后限流 |
| Q3 | ROS 节点放在哪 | A. 新建 `ros2_ws/src/web_bridge` 独立包，跨进程<br>B. 在 aiagent 主进程里用 `rclpy` 直接订阅 | **B** — 主进程已经是 asyncio，rclpy 有 async executor，可省 IPC，少一个进程要管 |
| Q4 | FAST-LIO 的 fixed_frame 是什么？ | `camera_init` / `map` / `body` / 其他 | **需你确认**——决定前端坐标系和 path/odometry 是否要做变换 |
| Q5 | 是否要叠加实时 scan (`/a/cloud_registered`)？ | A. 只显示累积地图<br>B. 累积地图 + 实时 scan（不同颜色） | **B** — 实时 scan 让"机器人在动"这件事看得见，体验差很多 |
| Q6 | 点云上色策略 | A. 单色<br>B. 按 Z 高度渐变（rainbow）<br>C. 按 intensity（如果点云带 intensity 字段） | **B** — 效果好且不依赖 intensity 字段；rviz 默认也是这个 |
| Q7 | 地图传输策略 | A. 每次推送全量（覆盖式）<br>B. 增量（diff，只推新增点） | **A** — 实现简单，5cm voxel 后全量大小可控；FAST-LIO 的 `/a/Laser_map` 本身就是累积发布 |
| Q8 | three.js 引入方式 | A. CDN<br>B. 本地 vendored 到 `web_static/vendor/` | **B** — 开发板/平板可能在内网/弱网环境，离线可用更稳 |
| Q9 | 平板和 DK2500 的网络拓扑 | 同 Wi-Fi 直连 / 路由转发 / 其他 | **需你确认** — 影响是否要处理 NAT、是否能用 mDNS 发现 |
| Q10 | SLAM 节点未启动时的行为 | A. 页面显示"无数据"占位<br>B. 隐藏入口 | **A** — 简单可靠，不用做服务发现 |
| Q11 | 访问权限 | 局域网内任意设备可访问 / 加 token | 沿用现有 console 策略（**当前是无认证**，建议比赛期先不动） |
| Q12 | 前端是否要交互工具 | 仅 OrbitControls / 加测距/截图/隐藏图层开关 | **MVP 仅 OrbitControls**，工具栏放 v2 |

---

## 1. 总体架构

```
┌──────────────────── DK2500 (Ubuntu 22.04) ─────────────────────┐
│                                                                 │
│   FAST-LIO ─publish─► /a/Laser_map  /a/cloud_registered         │
│                       /a/Odometry   /a/path                     │
│                              │                                  │
│                              ▼                                  │
│   ┌─────────── aiagent (Python, asyncio) ─────────────────┐     │
│   │                                                       │     │
│   │   SlamBridge (rclpy 节点)                             │     │
│   │     ├─ subscribe + voxel downsample (Open3D / PCL)    │     │
│   │     └─ encode → bytes ──┐                             │     │
│   │                         ▼                             │     │
│   │   WebServer (FastAPI)                                 │     │
│   │     ├─ /ws         (现有 console)                     │     │
│   │     ├─ /ws/slam    (新增，二进制帧)                   │     │
│   │     ├─ /slam       (新页面)                           │     │
│   │     └─ /static/slam/*                                 │     │
│   └───────────────────────────────────────────────────────┘     │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ Wi-Fi (同 LAN)
                                  ▼
┌──────────────── 骁龙 870 平板（浏览器）────────────────────────┐
│   /slam 页面                                                   │
│     ├─ three.js (WebGL)                                        │
│     ├─ Points × 2  (累积地图 / 实时 scan)                      │
│     ├─ Line        (轨迹)                                      │
│     ├─ ArrowHelper (当前位姿)                                  │
│     └─ OrbitControls                                           │
└────────────────────────────────────────────────────────────────┘
```

---

## 2. 后端模块

### 2.1 新增文件
- `src/display/slam_bridge.py` — rclpy 节点，订阅 + 降采样 + 编码 + 推送
- `src/display/web_static/slam.html` — SLAM 页面入口
- `src/display/web_static/slam.js` — three.js 渲染
- `src/display/web_static/slam.css`
- `src/display/web_static/vendor/three.min.js` 等

### 2.2 修改文件
- `src/display/web_server.py`
  - 新增 `/slam` 路由返回 `slam.html`
  - 新增 `/ws/slam` WebSocket endpoint
  - 新增 `broadcast_slam_bytes(payload: bytes)` 方法（独立连接集合）
- `src/application.py`（待确认是哪里启动 WebDisplay 的）
  - 启动时一并初始化 `SlamBridge`

### 2.3 SlamBridge 职责
| 订阅 topic | 处理 | 推送频率 | 编码 |
|-----------|------|----------|------|
| `/a/Laser_map` (PointCloud2) | voxel 5cm 降采样 | 1 Hz | 二进制：`[u8 channel=0x01][u32 N][N×3×float32 XYZ]` |
| `/a/cloud_registered` (PointCloud2) | voxel 10cm 降采样 | 5 Hz | 二进制：`[u8 channel=0x02][u32 N][N×3×float32 XYZ]` |
| `/a/Odometry` (Odometry) | 提取 pose | 10 Hz | 二进制：`[u8 channel=0x03][7×float32 x y z qx qy qz qw]` |
| `/a/path` (Path) | 抽稀（每 10 个点取 1） | on change | 二进制：`[u8 channel=0x04][u32 N][N×3×float32]` |

**降采样实现**：用 `open3d` 最简单（`voxel_down_sample`），如果不想加依赖也可以手写 numpy hash-grid。
**rclpy + asyncio 整合**：用 `MultiThreadedExecutor` 跑 ROS 回调，回调里 `asyncio.run_coroutine_threadsafe(server.broadcast_slam_bytes(...), loop)`。

### 2.4 限流与背压
- 每个 WS 连接维护一个 `asyncio.Queue`（maxsize=2），满了就丢最旧帧——保证后端不会因为客户端慢被堆死
- 地图全量帧用一个独立"latest"槽位，新帧覆盖旧帧

---

## 3. 前端模块

### 3.1 页面布局（MVP）
```
┌─────────────────────────────────────────┐
│ [← 返回] SLAM Viewer       [⚪ 已连接]  │  ← 顶部窄条
├─────────────────────────────────────────┤
│                                         │
│                                         │
│            (three.js canvas)            │  ← 占满剩余空间
│                                         │
│                                         │
├─────────────────────────────────────────┤
│ 地图点: 12.3万  位姿: x=1.2 y=0.5 yaw=30°│  ← 底部状态条
└─────────────────────────────────────────┘
```

### 3.2 渲染对象
| 对象 | three.js 类型 | 数据来源 |
|------|--------------|----------|
| 累积地图 | `Points` + `BufferGeometry`（动态 attribute） | channel 0x01 |
| 实时 scan | `Points` + 不同材质（更亮、更小） | channel 0x02 |
| 轨迹 | `Line` | channel 0x04 |
| 当前位姿 | `ArrowHelper` 或自定义 mesh | channel 0x03 |
| 坐标轴 | `AxesHelper` | 静态 |
| 网格地面 | `GridHelper` | 静态 |

### 3.3 交互
- 鼠标/触摸：`OrbitControls`（pan / zoom / orbit）
- 双击：重置视角到俯视
- 自动跟随相机（可选，v2）

### 3.4 性能预算
- 5cm voxel 后地图点数预估 < 30 万（室内场景），three.js 在骁龙 870 上 60fps 无压力
- 单帧 buffer 更新使用 `geometry.attributes.position.needsUpdate = true`，避免重建几何体

---

## 4. 联调与验证步骤

1. **后端 stub 验证**：先让 SlamBridge 不接 ROS，定时推假数据（一个旋转的方块点云），确认通道、编码、前端解析对得上
2. **接入真实 topic**：在 DK2500 上跑 FAST-LIO（或回放 bag），观察推送频率与丢帧
3. **平板验证**：骁龙 870 平板浏览器打开 `http://<DK2500_IP>:8080/slam`，确认渲染流畅
4. **压力场景**：建图 30 分钟后地图点数膨胀情况，必要时把 voxel size 调大

---

## 5. 风险与未决事项

- **rclpy + asyncio 整合的稳定性**：之前 aiagent 主进程没集成过 rclpy，需要小心 executor 生命周期与 Ctrl-C 信号处理。如果踩坑严重，回退方案是 Q3 的 A（独立 ROS 节点 + 共享内存或 ZeroMQ 推到 web 进程）。
- **/a/path 数据量**：FAST-LIO 的 path 会一直累积，长时间运行后单条消息可能 MB 级。抽稀策略可能要更激进（保留拐点而不是固定步长）。
- **PointCloud2 字段格式**：需要确认 `/a/Laser_map` 的 fields 是 `x y z` 还是带 `intensity`/`rgb`，影响解析代码。
- **跨进程隔离**：如果 SlamBridge 把 aiagent 主进程拖崩了会同时影响语音对话功能。Q3 的 A 方案在这点上更安全。

---

## 6. 工作量再估

基于以上设计粒度，AI 辅助下的重新估算：

| 阶段 | 工时 |
|------|------|
| 后端 SlamBridge + WebServer 改动 | 0.5 天 |
| 前端 slam.html / slam.js 骨架 | 0.5 天 |
| 联调与效果调优（颜色、点大小、相机参数） | 0.5 ~ 1 天 |
| **合计** | **1.5 ~ 2 天** |

如果 Q3 选 A（独立 ROS 节点）多加 0.5 天通信层。

---

## 7. 下一步

请按 §0 的表格逐项确认/修改。确认完后我会：
1. 更新本文档为 v1.0（移除"待确认"标记）
2. 按章节 §2、§3 进入编码

