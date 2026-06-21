# SLAM Web Viewer — 基底图叠加 / 渲染优化 / 禁飞区 设计文档

> **版本**：v4（AI 可执行版）
> **目标读者**：AI 编码工具（Claude Code / Cursor / Copilot agent 等）
> **目标**：在现有 `/slam` 页面基础上,叠加预建全局地图(`global_map_ds.pcd`),叠加飞机增量探索点云,并为禁飞区交互预留接口。
> **状态**：**P0 H1-H10 已验收;P1 前端禁飞区已落地;P2 下发框架已搭好(2026-05-20)**。实际 ROS publish 等飞控消息契约确认。
> **关联文档**：`SLAM_WEB_VIEWER_DESIGN.md`(基础框架)。

---

## 当前进度速查（2026-05-20）

| 阶段 | 状态 | 备注 |
|---|---|---|
| §H1-H5 新增模块文件 | ✅ 已完成 | `maps/global_map_ds.pcd` + `pcd_parser.js` + `voxel_infill.js` + `voxel_accumulator.js` + `utils.js` |
| §H6-H8 改 html/js/css | ✅ 已完成 | `slam.html` / `slam.js` / `slam.css`,详见 §C |
| §H9 开发板 stub 模式验收 | ✅ 已通过 | 2026-05-20 开发板/平板侧测试通过 |
| §H10 性能 5 分钟观察 | ✅ 已通过 | 2026-05-20 开发板/平板侧测试通过 |
| **禁飞区 UI(§I.P1)** | ✅ 已完成 | 前端绘制、两段式浏览/框选、列表、命名、删除、清空、高度 slider、localStorage |
| 禁飞区下发(§I.P2) | 🧩 框架已搭 | `POST/GET /api/noflyzone` + `NoFlyZoneBridge` 占位;待飞控确认 msg 后实际 publish |
| 长期(§I.P3) | ⏸ 未开始 | 多飞机/违规检测,远期 |

**预编译自测**(PC Node 端,不代表开发板 WebView 行为,仅作算法正确性 sanity check):
- `VoxelAccumulator` 去重 + cap=15w eviction ✅

**⚠️ 2026-06-03 换图更新(重要)**:`global_map_ds.pcd` 已替换为新场地数据,格式/坐标系不变,但**规模大幅变大**。本文中所有基于旧 demo(5587 点)的数字已过时,以下为新图实测:
- `pointCount = 21404`(旧 5587);文件 342,652 字节(旧 89,578)
- bbox: x ∈ [-89.98, 22.41]、y ∈ [-81.14, 13.56]、z ∈ [-3.54, 6.39](跨度约 112m × 95m × 10m,旧图才 ~42m × 24m)
- `voxelInfill(positions, 0.1)` → **245,725** 虚拟点(远超预算)→ 已把 `voxelInfillSize` 调到 **0.2**,降到 76,842 虚拟点、基底合计 ~9.8 万,回到 30k-80k 预算内
- 因地图变大,`slam.js` 做了一组适配(见 §C.6,纯前端、点云真实坐标不变):网格/坐标轴留在原点放大、相机 far 500→4000、maxDistance 放开、**默认与双击复位=坐标系原点正上方俯视**(按视口比例框住整图)、**基底图点改屏幕恒定像素**(否则百米俯视点会缩成亚像素看着像空的)

---

## 阅读顺序建议（针对 AI 工具）

1. 先读 §1-§2 理解约束和前提
2. 读 §A 摸清现有代码状态（**不要跳过**,否则会乱改）
3. 读 §3-§9 理解设计
4. 读 §B 抄出新模块骨架
5. 读 §C 按 patch 修改现有文件
6. 读 §D 验收
7. 读 §E 边界规范(**违反会被拒**)
8. 不懂的回到 §F 决策日志查"为什么"
9. 离线/无 ROS 环境用 §G 测试

---

## 1. 背景与约束

### 1.1 业务目标

- 平板 `/slam` 页面**起飞前**呈现一张预建的全局地图(`global_map_ds.pcd`)
- 飞机起飞后实时叠加飞机的 SLAM 输出,形成"预建图打底 + 飞机增量探索"的效果
- 后续支持在地图上**框选禁飞区**,把坐标下发给飞机(本次只留接口)

### 1.2 硬约束

| 约束 | 说明 |
|---|---|
| 通信带宽 | 点云已经压到极限,**不能再加密度**,只能从渲染端补 |
| PCD 文件 | 当前 demo,以后会换。**格式不变**(binary, FIELDS=x y z intensity, float32)、**坐标系不变**,只是场地数据替换 |
| 飞机端 topic | 已固定: `/a/Laser_map`(累积地图)、`/a/drone_0_cloud_registered_world`(实时帧)、`/a/drone_0_Odometry_world`(位姿)、`/a/path_world`(轨迹) |
| 现有 `slam.js` | 已有 MAP/SCAN/ODOM/PATH 四个 channel,**复用不重写** |
| 平板硬件 | 骁龙 870 + Adreno 650,Android WebView |
| 背景色 | 现有 `0x06080c`(接近纯黑,略带蓝绿),**沿用** |
| 飞机数量 | 当前 1 架,后续可能扩多机(本次不做) |

---

## 2. 已确认的前提（来自用户与队友沟通）

1. 飞机的 `/a/Laser_map` 和 `/a/drone_0_cloud_registered_world` **已经在基底图 frame 下**(队友确认)。`SLAM_FIXED_FRAME = "a/camera_init"` 这个 frame 名义上就是基底图的 frame。
2. 重定位误差是 **B 类(对齐残差,固定偏移)**,大小约 10cm。
3. **但偏移方向是随机的**(每次重定位不同) → **不能写死补偿矩阵**,**本版本不做坐标系补偿**,先观察效果。
4. 用户已点头的设计决策(详见 §F 决策日志):
   - 渲染优化方案 A+B+C+D 全上
   - 累积策略改为"体素去重 + 数量上限"(替换现有 25 帧滑动窗口)
   - 配色:基底图单色冷灰,累积层**保留现有 height-rainbow**,实时扫描沿用现有橙色
   - 禁飞区本次只留接口(`enableNoFlyZoneDraw = false`)
   - 文档作为 AI 执行指令,**不允许 AI 自作主张越界**(见 §E)

---

## 3. 总体架构 — 四层叠加

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 0  基底图 (global_map_ds.pcd)         [新增]         │
│           静态、一次加载、暗钢蓝灰单色、打底                  │
├─────────────────────────────────────────────────────────────┤
│  Layer 1  飞机累积地图 (/a/Laser_map)        [已有,改算法]   │
│           体素去重 + 数量上限、height-rainbow 上色            │
├─────────────────────────────────────────────────────────────┤
│  Layer 2  飞机当前帧扫描 (/a/drone_0_cloud_registered_world)  │
│                                              [已有,微调]     │
│           25 帧滑动窗口、橙色单色                             │
├─────────────────────────────────────────────────────────────┤
│  Layer 3  飞机轨迹 + 当前位姿                [已有,不动]     │
│           /a/path_world 绿色折线 + /a/drone_0_Odometry_world 粉红箭头 │
└─────────────────────────────────────────────────────────────┘
```

**视觉语义**：
- 基底图(灰冷调) = "已知地图,静态"
- 累积层(rainbow) = "飞机探到的,带高度结构信息"
- 实时扫描(橙) = "飞机现在正在看的"
- 轨迹+位姿(绿+粉) = "飞机走过哪、现在在哪"

---

## 4. 配色与材质规格

### 4.1 现有色（保留不动）

| 元素 | 颜色 | 说明 |
|---|---|---|
| 背景 | `0x06080c` | `slam.js:22` |
| 累积层(mapPoints) | `vertexColors: true` + `colorByHeight` rainbow | `slam.js:41-43`,height 从 zMin→zMax 映射 H=0.7→0 |
| 实时扫描(scanPoints) | `0xffaa33` | 暖橙,`slam.js:50-52` |
| Path 折线 | `0x33ff88` | 绿,`slam.js:65` |
| 位姿箭头 | `0xff4477` | 粉红,`slam.js:71` |
| AxesHelper / Grid | 默认 + `0x224466/0x142238` | `slam.js:32-35` |

### 4.2 新增（基底图）

```js
// 真实点(从 PCD 解析出的)
const baseMapMat = new THREE.PointsMaterial({
  size: 0.08,
  sizeAttenuation: true,
  color: 0x5a6878,      // 暗钢蓝灰
  opacity: 0.55,
  transparent: true,
  // map: discTexture, alphaTest: 0.5  // §F.3 妥协: 先不用 sprite,纯方点先跑通
});

// 体素补全的虚拟点(方案 D)
const baseMapInfillMat = new THREE.PointsMaterial({
  size: 0.05,
  sizeAttenuation: true,
  color: 0x5a6878,      // 同色但更小更透
  opacity: 0.25,
  transparent: true,
});
```

### 4.3 累积层材质修改（保留 rainbow,只改 size）

现有 `mapMat.size = 0.07`,**保持不变**。但点尺寸要 ≥ 10cm 这条约束适用于"单点抖动可见"的场景；累积层因为是密度叠加,可以小一点。

---

## 5. 稀疏点云渲染优化方案

### 5.1 采用方案 A + B + C + D 的组合

| 层 | A 大点尺寸 | B 圆盘 sprite | C 累积去重 | D 体素补全 |
|---|---|---|---|---|
| Layer 0 基底图 | ✅ size=0.08 | ⏸ 暂不做(§F.3) | — | ✅ voxel=0.1m |
| Layer 1 累积层 | ✅ size=0.07 | ⏸ 暂不做 | ✅ voxel=0.05m, cap=15万 | — |
| Layer 2 实时扫描 | ✅ size=0.07 | ⏸ 暂不做 | 沿用现有 25 帧窗口 | — |

### 5.2 方案 D 详细 — 基底图体素补全

启动时一次性处理:
1. 把基底图按 voxel size `0.1m` 体素化
2. 对每个有点的 voxel,在 26 邻居 voxel 中插入"虚拟点"(若邻居 voxel 原本没点)
3. 虚拟点用 `baseMapInfillMat`(更小、更透)渲染

**预期输出**(换图后,voxel=0.2m): 21,404 真实点 → ~76,800 虚拟点 → 总 ~9.8 万点渲染。(旧 demo 5587 点 / voxel 0.1m 时约 5.4 万虚拟点)

**算法骨架**(详细见 §B.2)：
```
realVoxels = Set of "ix,iy,iz" of all real points
infillVoxels = Set()
for each realVoxel (ix,iy,iz):
  for dx in -1..1, dy in -1..1, dz in -1..1:
    if (dx,dy,dz) == (0,0,0): continue
    key = "ix+dx,iy+dy,iz+dz"
    if key not in realVoxels:
      infillVoxels.add(key)
// 把每个 infillVoxel 中心点输出为虚拟点位置
```

**重要**: 虚拟点是"假数据"仅用于视觉填充。**禁止**把虚拟点回写到累积层或用于规划。

---

## 6. 累积策略 — 体素去重 + 数量上限

### 6.1 替换什么

**当前** `slam.js:56-60, 132-149` 实现的是"scan 滑动窗口"(25 帧 × 5Hz = 5 秒窗口),作用于 **Layer 2(scanPoints)**。

**注意范围**: 6.x 节描述的"体素累积"作用于 **Layer 1(mapPoints,累积地图)**,**不是替换 scan 窗口**。

具体说:
- Layer 1(mapPoints): **当前**`updatePoints` 直接 `geom.setAttribute('position', xyz)` 覆盖式更新 → **改为**体素去重 + 数量上限的累加。
- Layer 2(scanPoints): **保持** 25 帧滑动窗口不动。

### 6.2 算法

```
新一批点 batch 进入累积层:
  for each point (x,y,z) in batch:
    voxelKey = "${floor(x/V)},${floor(y/V)},${floor(z/V)}"  // V=0.05
    if accumulatedVoxels.has(voxelKey):
      continue  // 去重
    accumulatedVoxels.add(voxelKey, {x, y, z, insertOrder: nextId++})
  if accumulatedVoxels.size > MAX:
    evict oldest (按 insertOrder) until size == MAX
  rebuildBufferAttribute(accumulatedVoxels)
```

### 6.3 参数

```js
ACCUMULATE_VOXEL_SIZE = 0.05    // 5cm,比 10cm 误差小,保留细节
ACCUMULATE_MAX_POINTS = 150000  // 15 万
```

### 6.4 数据流变化

```
   ROS /a/Laser_map (PointCloud2)
        │
        ▼
   slam_bridge.py voxel_downsample (2cm)
        │
        ▼
   WebSocket CHAN.MAP (二进制帧)
        │
        ▼
   slam.js onBinary → CHAN.MAP
        │
        ▼  [新逻辑] 不再直接 setAttribute,而是:
   VoxelAccumulator.addBatch(xyz)
        │
        ▼
   VoxelAccumulator.getPositions() → Float32Array
        │
        ▼
   mapGeom.setAttribute('position', ...) + colorByHeight 重算
```

### 6.5 重要保留

`mapMat.vertexColors = true` 和 `colorByHeight` 高度上色逻辑**保留**。每次 `addBatch` 后要重算颜色(因为新加入的点 zMin/zMax 可能变)。

### 6.6 UI 配套

加一个"清除累积"按钮(`#btn-clear-accum`),点击调 `VoxelAccumulator.clear()`,起飞前清一次避免静止数据污染。状态栏新增"累积:N/15万"。

---

## 7. 坐标系对齐 — 已确认 + 处理策略

### 7.1 已确认

- 点云已在基底图 frame 下
- 误差类型: B 类对齐残差,~10cm,**方向随机**

### 7.2 本版本处理

- **不做静态补偿**(方向随机会补反)
- 点尺寸 ≥ 10cm 让漂移被"覆盖"
- `SLAM_CONFIG.baseMapTransform` 配置项保留为 `null`(等价 identity)
- 万一未来发现方向有规律(比如总是 +x),改 `baseMapTransform = [4x4 矩阵]` 启用,**不动代码**

### 7.3 用户应预期的现象（**不是 bug**）

- 飞机层和基底图整体偏 10cm
- 同一面墙可能看到"双重轮廓"
- 累积久了两层融合成 20cm 厚带,视觉上反而像"高密度"

---

## 8. 性能预算（骁龙 870 + Adreno 650 + WebView）

| 层 | 点数 | 备注 |
|---|---|---|
| 基底图真实点 | ~21,404 | PCD 解析（旧 demo 5,587） |
| 基底图虚拟点(方案 D) | ~76,800 | voxel=**0.2m**,26 邻居（0.1m 会到 24.6w，过载） |
| 累积层 | 上限 150,000 | 体素去重 0.05m |
| 实时扫描(25 帧窗口) | ~20,000 | 单帧 800 × 25 |
| 轨迹折线 | <10,000 顶点 | 长时间飞行可能要降采样 |
| **总计** | ~230,000 | 预计 40-50 FPS |

低性能模式 `SLAM_CONFIG.lowPerfMode = true`:
- 关闭方案 D
- 累积上限降到 8 万
- 预期 55-60 FPS

---

## 9. 禁飞区 P1 预留

本次**不实现 UI**,只:
1. 在 `SLAM_CONFIG` 加 `enableNoFlyZoneDraw: false` 占位
2. 在 `utils.js` 写好工具函数 `screenToWorldXY(event, camera, canvas, raycaster)`,P1 直接用

详细 P1/P2/P3 设计放在 §I。

---

## §A. 现有代码摘录（AI 必读）

### A.1 文件树位置

```
/home/kian/kian_project/aiagent/
├── src/display/
│   ├── slam_bridge.py            ← ROS 订阅与编码,后端
│   ├── slam_constants.py         ← 所有 topic / channel id / 参数常量
│   ├── web_server.py             ← FastAPI 路由,/static 在 line 126 mount
│   └── web_static/               ← 前端静态资源根目录
│       ├── slam.html             ← /slam 页面
│       ├── slam.css              ← /slam 样式
│       ├── slam.js               ← /slam 主逻辑(241 行)
│       ├── vendor/               ← three.js 本地副本
│       │   ├── three.module.min.js
│       │   └── three-addons/
│       └── maps/                 ← [本次新增] 基底地图静态资源
│           └── global_map_ds.pcd ← [本次新增] 拷贝自 ~/kian_project/
└── project_markdown/
    └── slam_base_map_and_nfz_design.md  ← 本文档
```

### A.2 slam.js 关键片段（截至 v4 编写时,共 241 行）

#### CHAN 协议定义 (line 1-14)
```js
/**
 * 二进制协议 (与 src/display/slam_bridge.py 一致):
 *   channel 0x01 MAP   : [u8][u32 N][N*3*float32 XYZ]
 *   channel 0x02 SCAN  : [u8][u32 N][N*3*float32 XYZ]
 *   channel 0x03 ODOM  : [u8][7*float32 x y z qx qy qz qw]
 *   channel 0x04 PATH  : [u8][u32 N][N*3*float32 XYZ]
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const CHAN = { MAP: 0x01, SCAN: 0x02, ODOM: 0x03, PATH: 0x04 };
```

#### 场景与相机 (line 16-35)
```js
const canvas = document.getElementById('slam-canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x06080c);
const camera = new THREE.PerspectiveCamera(60, 1, 0.05, 500);
camera.position.set(8, -8, 6);
camera.up.set(0, 0, 1);                    // Z-up
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
scene.add(new THREE.AxesHelper(1.0));
const grid = new THREE.GridHelper(20, 20, 0x224466, 0x142238);
grid.rotation.x = Math.PI / 2;
scene.add(grid);
```

#### 累积地图层 (line 37-45) — 本次要改累积逻辑,但 mat 保留
```js
const mapGeom = new THREE.BufferGeometry();
mapGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
mapGeom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(0), 3));
const mapMat = new THREE.PointsMaterial({
  size: 0.07, vertexColors: true, sizeAttenuation: true,
});
const mapPoints = new THREE.Points(mapGeom, mapMat);
scene.add(mapPoints);
```

#### 实时 scan 层 (line 47-60) — 本次不动
```js
const scanGeom = new THREE.BufferGeometry();
scanGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const scanMat = new THREE.PointsMaterial({
  size: 0.07, color: 0xffaa33, sizeAttenuation: true,
});
const scanPoints = new THREE.Points(scanGeom, scanMat);
scene.add(scanPoints);
const SCAN_WINDOW_FRAMES = 25;
const scanFrames = [];
```

#### Path + 位姿 (line 62-74) — 本次不动
```js
const pathGeom = new THREE.BufferGeometry();
pathGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const pathMat = new THREE.LineBasicMaterial({ color: 0x33ff88 });
const pathLine = new THREE.Line(pathGeom, pathMat);
scene.add(pathLine);
const poseArrow = new THREE.ArrowHelper(
  new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0),
  0.6, 0xff4477, 0.18, 0.1,
);
scene.add(poseArrow);
```

#### 状态条 DOM 引用 (line 76-81)
```js
const statMap  = document.getElementById('stat-map');
const statScan = document.getElementById('stat-scan');
const statPose = document.getElementById('stat-pose');
const statFps  = document.getElementById('stat-fps');
const connBadge = document.querySelector('.conn-badge');
```

#### colorByHeight (line 83-93) — 累积层用,本次保留
```js
function colorByHeight(positions, zMin, zMax, out) {
  const span = Math.max(1e-3, zMax - zMin);
  for (let i = 0, j = 0; i < positions.length; i += 3, j += 3) {
    const t = (positions[i + 2] - zMin) / span;
    const h = (1 - t) * 0.7;
    const c = new THREE.Color().setHSL(h, 1.0, 0.55);
    out[j] = c.r; out[j + 1] = c.g; out[j + 2] = c.b;
  }
}
```

#### 二进制分发 (line 95-106)
```js
function onBinary(buf) {
  const dv = new DataView(buf);
  const channel = dv.getUint8(0);
  switch (channel) {
    case CHAN.MAP:  return updatePoints(buf, mapGeom, true);
    case CHAN.SCAN: return updateScanWindow(buf);
    case CHAN.ODOM: return updateOdom(dv);
    case CHAN.PATH: return updatePath(buf);
  }
}
```

#### updatePoints (line 108-130) — 本次要改累积层这一支
```js
function updatePoints(buf, geom, withColor) {
  const dv = new DataView(buf);
  const n = dv.getUint32(1, true);
  // header 5 字节, float32 4 字节对齐, 必须 slice
  const xyz = new Float32Array(buf.slice(5, 5 + n * 12));
  geom.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
  if (withColor) {
    let zMin = Infinity, zMax = -Infinity;
    for (let i = 2; i < xyz.length; i += 3) {
      if (xyz[i] < zMin) zMin = xyz[i];
      if (xyz[i] > zMax) zMax = xyz[i];
    }
    const colors = new Float32Array(n * 3);
    colorByHeight(xyz, zMin, zMax, colors);
    geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    statMap.textContent = `地图点: ${n.toLocaleString()}`;
  } else {
    statScan.textContent = `实时点: ${n.toLocaleString()}`;
  }
  geom.computeBoundingSphere();
}
```

#### WebSocket 重连 (line 180-204) — 本次不动
```js
let ws = null;
let reconnectTimer = null;
function connect() {
  if (ws && ws.readyState <= 1) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/slam`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { /* ... */ };
  ws.onclose = () => { /* ... reconnectTimer = setTimeout(connect, 2000); */ };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) onBinary(e.data);
  };
}
```

#### 启动 (line 234-241)
```js
canvas.addEventListener('dblclick', () => {
  camera.position.set(0, 0, 15);
  controls.target.set(0, 0, 0);
});
connect();
requestAnimationFrame(tick);
```

### A.3 slam.html 全文（共 50 行）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>SLAM Viewer</title>
  <link rel="stylesheet" href="/static/slam.css">
  <script>
    if (new URLSearchParams(location.search).get('embedded') === '1') {
      document.documentElement.classList.add('embedded');
    }
  </script>
</head>
<body>
<div id="slam-app">
  <div class="slam-topbar">
    <a class="back-link" href="/">← 返回</a>
    <div class="title">SLAM Viewer</div>
    <div class="conn-badge disconnected">OFFLINE</div>
  </div>
  <canvas id="slam-canvas"></canvas>
  <div class="slam-statusbar">
    <span id="stat-map">地图点: -</span>
    <span id="stat-scan">实时点: -</span>
    <span id="stat-pose">位姿: -</span>
    <span id="stat-fps">FPS: -</span>
  </div>
</div>
<script type="importmap">
{
  "imports": {
    "three": "/static/vendor/three.module.min.js",
    "three/addons/": "/static/vendor/three-addons/"
  }
}
</script>
<script type="module" src="/static/slam.js"></script>
</body>
</html>
```

### A.4 slam_bridge.py 关键事实

- 后端二进制编码 `encode_points(channel, xyz)` 输出 `[u8 channel][u32 N (LE)][N*3*float32 XYZ]` —— **与前端 `updatePoints` 完全一致**
- 已有 stub 模式: 设 `AIAGENT_SLAM_STUB=1` 环境变量,会推假数据(地图螺旋、scan 球团、odom 圆轨迹) —— §G 离线测试用这个
- 通过 `web_server.broadcast_slam_bytes(payload)` 单一方法推送
- ROS 模式下:
  - map_qos depth=1, scan_qos depth=5, odom_qos depth=10,全部 BEST_EFFORT
  - `_voxel_downsample` numpy 实现,voxel size 由 `slam_constants.py` 控制

### A.5 slam_constants.py 关键常量

```python
SLAM_TOPIC_MAP = "/a/Laser_map"
SLAM_TOPIC_SCAN = "/a/drone_0_cloud_registered_world"
SLAM_TOPIC_ODOM = "/a/drone_0_Odometry_world"
SLAM_TOPIC_PATH = "/a/path_world"
SLAM_FIXED_FRAME = "a/camera_init"      # 与基底图同 frame(已确认)
SLAM_MAP_VOXEL_SIZE = 0.02
SLAM_SCAN_VOXEL_SIZE = 0.03
SLAM_MAP_MAX_HZ = 1.0
SLAM_SCAN_MAX_HZ = 5.0
SLAM_ODOM_MAX_HZ = 10.0
SLAM_PATH_DECIMATE = 10
CHAN_MAP = 0x01
CHAN_SCAN = 0x02
CHAN_ODOM = 0x03
CHAN_PATH = 0x04
SLAM_STUB_FALLBACK_WHEN_NO_ROS = True
```

### A.6 web_server.py 静态资源挂载（line 126）

```python
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# STATIC_DIR = Path(__file__).parent / "web_static"
```

**重要结论**: `web_static/maps/global_map_ds.pcd` **自动**通过 `/static/maps/global_map_ds.pcd` 可访问,**不需要改 web_server.py**。

### A.7 PCD 文件源信息

```
格式:   binary（换图后不变）
header: FIELDS x y z intensity
        SIZE 4 4 4 4
        TYPE F F F F
        COUNT 1 1 1 1
        WIDTH 21404        # 旧 demo 为 5587
        HEIGHT 1
        POINTS 21404
        DATA binary
```

每点 16 字节(4 个 float32),共 21404 × 16 = 342,464 字节数据段 + 188 字节 header = 342,652 字节文件。
（2026-06-03 换图：新数据由微信传入后替换，格式与坐标系与旧 demo 完全一致，仅场地规模变大。旧文件已备份为 `web_static/maps/global_map_ds.pcd.bak.5587`。）

---

## §B. 新模块完整骨架（AI 直接抄出来填充实现）

> 所有新模块都放在 `src/display/web_static/` 下,**ES module**,**用 importmap 中已注册的 'three' 名**。

### B.1 `pcd_parser.js`

```js
/**
 * PCD v0.7 binary 解析器（最小实现,只支持本项目用到的格式）
 *
 * 支持:
 *   - DATA binary
 *   - FIELDS 包含 x y z（顺序任意,intensity 可有可无）
 *   - SIZE/TYPE: 所有字段 float32 (4F)
 *
 * 不支持(遇到抛错):
 *   - DATA ascii 或 binary_compressed
 *   - 非 float32 字段(uint8 rgb 等)
 *
 * 设计原则: 按 header 解析字段偏移,不 hardcode 16 字节/点,
 *          换 PCD 时只要还是同类格式就能直接用。
 */

/**
 * @typedef {Object} PCDResult
 * @property {Float32Array} positions     xyz interleaved, length = 3*N
 * @property {Float32Array|null} intensities  length = N, 无 intensity 字段则 null
 * @property {number} pointCount
 * @property {{min: [number,number,number], max: [number,number,number]}} bbox
 */

/**
 * @param {ArrayBuffer} buf
 * @returns {PCDResult}
 * @throws {Error}
 */
export function parsePCD(buf) {
  // ── 1. 找到 header 结尾(第一个 \n 之后 DATA <type>\n)
  //    用 TextDecoder 解前 2KB,扫到 "DATA xxx\n" 行
  // ── 2. 解析 header 各字段
  //    必读: FIELDS, SIZE, TYPE, COUNT, WIDTH/POINTS, DATA
  // ── 3. 计算 stride 和每字段 offset
  //    stride = Σ(SIZE[i] * COUNT[i])
  //    xOffset/yOffset/zOffset/iOffset 根据 FIELDS 索引求
  // ── 4. 校验
  //    DATA == 'binary' 否则抛错
  //    x/y/z 三个字段都存在,且 SIZE=4 TYPE=F COUNT=1 否则抛错
  //    intensity 字段可选,存在时也要 SIZE=4 TYPE=F COUNT=1
  // ── 5. 数据段切片
  //    dataStart = header 字节长度
  //    点数 N = POINTS(优先) 否则 WIDTH*HEIGHT
  //    用 DataView 按 little-endian 读 N 个点
  // ── 6. 输出 positions / intensities / bbox
  //    bbox 顺便扫一遍计算
  throw new Error('TODO');
}
```

### B.2 `voxel_infill.js`

```js
/**
 * 体素邻居补全 — 给稀疏点云"加密度"的视觉填充。
 *
 * 算法:
 *   1. 把所有真实点按 voxelSize 分桶,得到占用 voxel 集合 S_real
 *   2. 对 S_real 中每个 voxel,枚举 26 个邻居 voxel
 *   3. 若邻居 voxel 不在 S_real 中,产出一个虚拟点(取邻居 voxel 中心坐标)
 *   4. 去重: 同一虚拟 voxel 只输出一次
 *
 * 使用约束:
 *   - 虚拟点仅供渲染,**禁止**用于规划/累积/导出
 *   - 输入点数建议 < 5 万,5 万以上请改用 Web Worker(本次不做)
 */

/**
 * @param {Float32Array} positions    xyz interleaved, length = 3*N
 * @param {number} voxelSize          米,默认 0.1
 * @returns {Float32Array}            虚拟点 xyz interleaved
 */
export function voxelInfill(positions, voxelSize = 0.1) {
  // 1. 真实 voxel 集合
  //    key = `${ix},${iy},${iz}` (string),value 不重要
  //    ix = Math.floor(x / voxelSize)
  // 2. 收集虚拟 voxel
  //    Set<string>,遍历真实 voxel × 26 邻居,跳过已在真实集合的
  // 3. 输出
  //    for each virtual key (ix,iy,iz):
  //      cx = (ix + 0.5) * voxelSize
  //      cy = (iy + 0.5) * voxelSize
  //      cz = (iz + 0.5) * voxelSize
  //      push 到 out
  throw new Error('TODO');
}
```

### B.3 `voxel_accumulator.js`

```js
/**
 * 体素去重 + 数量上限的点云累积器(替代直接 setAttribute 覆盖)。
 *
 * 行为:
 *   - addBatch(xyz): 新一批点进来,逐点算 voxel key,已存在则丢弃,
 *                    否则插入(带递增 insertOrder)
 *   - 超出 max 时按 insertOrder 删最早的(chunked 删除避免单帧抖动)
 *   - getPositions(): 返回当前所有点的 Float32Array(每帧重建,5万级别可接受)
 *   - clear(): 全清(配套"清除累积"按钮)
 *
 * 实现要点:
 *   - 内部用 Map<voxelKey, {x,y,z,order}> 存储
 *   - chunked 删除: 当 size > max 时一次最多删 max*0.05(避免大块 free 卡顿)
 */

export class VoxelAccumulator {
  /**
   * @param {number} voxelSize  米,默认 0.05
   * @param {number} max        最大点数,默认 150000
   */
  constructor(voxelSize = 0.05, max = 150000) {
    this.voxelSize = voxelSize;
    this.max = max;
    this._map = new Map();      // key -> {x,y,z,order}
    this._nextOrder = 0;
    this._dirty = false;        // 是否有变更,getPositions 时按需 rebuild
    this._cache = null;         // 上次 rebuild 的 Float32Array
  }

  /**
   * @param {Float32Array} xyz  xyz interleaved
   */
  addBatch(xyz) {
    const V = this.voxelSize;
    for (let i = 0; i < xyz.length; i += 3) {
      const x = xyz[i], y = xyz[i+1], z = xyz[i+2];
      const ix = Math.floor(x / V), iy = Math.floor(y / V), iz = Math.floor(z / V);
      const key = `${ix},${iy},${iz}`;
      if (this._map.has(key)) continue;
      this._map.set(key, { x, y, z, order: this._nextOrder++ });
    }
    if (this._map.size > this.max) this._evictOldest();
    this._dirty = true;
  }

  _evictOldest() {
    // chunked: 一次最多删 max*0.05
    const overflow = this._map.size - this.max;
    const chunk = Math.max(overflow, Math.floor(this.max * 0.05));
    const entries = Array.from(this._map.entries())
      .sort((a, b) => a[1].order - b[1].order)
      .slice(0, chunk);
    for (const [key] of entries) this._map.delete(key);
  }

  /**
   * @returns {Float32Array}
   */
  getPositions() {
    if (!this._dirty && this._cache) return this._cache;
    const out = new Float32Array(this._map.size * 3);
    let i = 0;
    for (const { x, y, z } of this._map.values()) {
      out[i++] = x; out[i++] = y; out[i++] = z;
    }
    this._cache = out;
    this._dirty = false;
    return out;
  }

  size() { return this._map.size; }

  clear() {
    this._map.clear();
    this._nextOrder = 0;
    this._cache = null;
    this._dirty = true;
  }
}
```

### B.4 `utils.js`（P1 预留,本次只写函数体,不用上）

```js
import * as THREE from 'three';

/**
 * 屏幕坐标 → 世界坐标(投影到 z=0 平面)。
 * P1 禁飞区绘制要用。
 *
 * @param {MouseEvent|PointerEvent|TouchEvent} event
 * @param {THREE.PerspectiveCamera} camera
 * @param {HTMLCanvasElement} canvas
 * @param {THREE.Raycaster} [raycaster]   复用避免每次 new
 * @returns {THREE.Vector3|null}          交点,若射线和 z=0 平面无交则 null
 */
export function screenToWorldXY(event, camera, canvas, raycaster) {
  const rc = raycaster || new THREE.Raycaster();
  const rect = canvas.getBoundingClientRect();
  const cx = event.clientX ?? event.touches?.[0]?.clientX;
  const cy = event.clientY ?? event.touches?.[0]?.clientY;
  if (cx == null || cy == null) return null;
  const ndc = new THREE.Vector2(
    ((cx - rect.left) / rect.width) * 2 - 1,
    -((cy - rect.top) / rect.height) * 2 + 1,
  );
  rc.setFromCamera(ndc, camera);
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  const target = new THREE.Vector3();
  return rc.ray.intersectPlane(plane, target) ? target : null;
}
```

---

## §C. 修改 patch（AI 直接照做,不要重新设计）

### C.1 文件操作清单

| 操作 | 路径 |
|---|---|
| 复制 | `/home/kian/kian_project/global_map_ds.pcd` → `src/display/web_static/maps/global_map_ds.pcd` |
| 新建目录 | `src/display/web_static/maps/` |
| 新建文件 | `src/display/web_static/pcd_parser.js`(按 §B.1) |
| 新建文件 | `src/display/web_static/voxel_infill.js`(按 §B.2) |
| 新建文件 | `src/display/web_static/voxel_accumulator.js`(按 §B.3) |
| 新建文件 | `src/display/web_static/utils.js`(按 §B.4) |
| 修改 | `src/display/web_static/slam.html`(见 §C.3) |
| 修改 | `src/display/web_static/slam.js`(见 §C.4) |
| 修改 | `src/display/web_static/slam.css`(见 §C.5,可选) |
| **不改** | `src/display/web_server.py` (`/static` 已通配,见 §A.6) |
| **不改** | `src/display/slam_bridge.py` (本次纯前端) |
| **不改** | `src/display/slam_constants.py` (本次不动 ROS 端) |

### C.2 复制 PCD 命令

```bash
mkdir -p /home/kian/kian_project/aiagent/src/display/web_static/maps
cp /home/kian/kian_project/global_map_ds.pcd \
   /home/kian/kian_project/aiagent/src/display/web_static/maps/global_map_ds.pcd
```

验证: `curl -I http://localhost:<port>/static/maps/global_map_ds.pcd` 应返回 200。

### C.3 修改 `slam.html`

**1) 在 `</head>` 之前注入 SLAM_CONFIG**(line 12 之后):

```html
<script>
  window.SLAM_CONFIG = {
    baseMapFile: 'global_map_ds.pcd',
    baseMapTransform: null,           // 4x4 数组 (Three.js 行主序展开) 或 null = identity
    enableVoxelInfill: true,
    voxelInfillSize: 0.1,             // 米
    accumulateVoxelSize: 0.05,        // 米
    accumulateMaxPoints: 150000,
    enableNoFlyZoneDraw: false,       // P1 占位
    lowPerfMode: false,
  };
</script>
```

**2) 在状态栏加"累积"和"清除累积"按钮**(line 25-32 范围):

把现有
```html
  <div class="slam-statusbar">
    <span id="stat-map">地图点: -</span>
    <span id="stat-scan">实时点: -</span>
    <span id="stat-pose">位姿: -</span>
    <span id="stat-fps">FPS: -</span>
  </div>
```
改为:
```html
  <div class="slam-statusbar">
    <span id="stat-base">基底: -</span>
    <span id="stat-map">累积: -</span>
    <span id="stat-scan">实时: -</span>
    <span id="stat-pose">位姿: -</span>
    <span id="stat-fps">FPS: -</span>
    <button id="btn-clear-accum" class="slam-btn">清除累积</button>
  </div>
```

### C.4 修改 `slam.js`

整体策略: **在现有代码基础上插入,不删除已有功能**(除了把 `updatePoints` 中累积层那一支改成走 accumulator)。

**1) 顶部 import 区(line 12 之后)新增**:
```js
import { parsePCD } from '/static/pcd_parser.js';
import { voxelInfill } from '/static/voxel_infill.js';
import { VoxelAccumulator } from '/static/voxel_accumulator.js';
const CFG = window.SLAM_CONFIG || {};
```

**2) 在 `scene.add(grid)` 之后(line 35 后)插入基底图层定义**:
```js
// ===== 基底图 (静态预建地图) =====
const baseMapGeom = new THREE.BufferGeometry();
baseMapGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const baseMapMat = new THREE.PointsMaterial({
  size: 0.08, sizeAttenuation: true,
  color: 0x5a6878, opacity: 0.55, transparent: true,
});
const baseMapPoints = new THREE.Points(baseMapGeom, baseMapMat);
scene.add(baseMapPoints);

const baseMapInfillGeom = new THREE.BufferGeometry();
baseMapInfillGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const baseMapInfillMat = new THREE.PointsMaterial({
  size: 0.05, sizeAttenuation: true,
  color: 0x5a6878, opacity: 0.25, transparent: true,
});
const baseMapInfillPoints = new THREE.Points(baseMapInfillGeom, baseMapInfillMat);
scene.add(baseMapInfillPoints);
```

**3) 在 `mapPoints` 定义之后(line 45 后)新增 accumulator**:
```js
const mapAccum = new VoxelAccumulator(
  CFG.accumulateVoxelSize ?? 0.05,
  CFG.accumulateMaxPoints ?? 150000,
);
```

**4) 状态条 DOM 引用(line 76-81 区域)增加**:
```js
const statBase = document.getElementById('stat-base');
const btnClearAccum = document.getElementById('btn-clear-accum');
btnClearAccum?.addEventListener('click', () => {
  mapAccum.clear();
  rebuildMapGeom();
  statMap.textContent = `累积: 0`;
});
```

**5) `updatePoints` 函数(line 108-130)改写,只针对 CHAN.MAP 走 accumulator**:

新增辅助:
```js
function rebuildMapGeom() {
  const xyz = mapAccum.getPositions();
  mapGeom.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
  // 重算 height rainbow
  let zMin = Infinity, zMax = -Infinity;
  for (let i = 2; i < xyz.length; i += 3) {
    if (xyz[i] < zMin) zMin = xyz[i];
    if (xyz[i] > zMax) zMax = xyz[i];
  }
  if (!isFinite(zMin)) { zMin = 0; zMax = 1; }
  const colors = new Float32Array(xyz.length);
  colorByHeight(xyz, zMin, zMax, colors);
  mapGeom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  mapGeom.computeBoundingSphere();
  statMap.textContent = `累积: ${mapAccum.size().toLocaleString()}/${(CFG.accumulateMaxPoints ?? 150000).toLocaleString()}`;
}

function updateMapAccumulated(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(1, true);
  const xyz = new Float32Array(buf.slice(5, 5 + n * 12));
  mapAccum.addBatch(xyz);
  rebuildMapGeom();
}
```

修改 dispatcher(line 100-106):
```js
switch (channel) {
  case CHAN.MAP:  return updateMapAccumulated(buf);   // 改这一行
  case CHAN.SCAN: return updateScanWindow(buf);
  case CHAN.ODOM: return updateOdom(dv);
  case CHAN.PATH: return updatePath(buf);
}
```

**保留** 原有 `updatePoints(buf, geom, withColor)` 函数不删,以备未来其他通道使用。

**6) 在 `connect()` 之前(line 240 前)新增基底图加载**:
```js
async function loadBaseMap() {
  try {
    const url = `/static/maps/${CFG.baseMapFile || 'global_map_ds.pcd'}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const buf = await resp.arrayBuffer();
    const { positions, pointCount, bbox } = parsePCD(buf);

    // 可选: 应用变换矩阵(本次方向随机,先 identity)
    // if (CFG.baseMapTransform) { ... }

    baseMapGeom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    baseMapGeom.computeBoundingSphere();

    let totalCount = pointCount;
    if (CFG.enableVoxelInfill !== false && !CFG.lowPerfMode) {
      const infill = voxelInfill(positions, CFG.voxelInfillSize ?? 0.1);
      baseMapInfillGeom.setAttribute('position', new THREE.BufferAttribute(infill, 3));
      baseMapInfillGeom.computeBoundingSphere();
      totalCount += infill.length / 3;
    }

    statBase.textContent = `基底: ${pointCount.toLocaleString()} (+${(totalCount - pointCount).toLocaleString()}补全)`;

    // 自适应相机: 看向 bbox 中心
    const cx = (bbox.min[0] + bbox.max[0]) / 2;
    const cy = (bbox.min[1] + bbox.max[1]) / 2;
    const cz = (bbox.min[2] + bbox.max[2]) / 2;
    const extent = Math.max(
      bbox.max[0] - bbox.min[0],
      bbox.max[1] - bbox.min[1],
      bbox.max[2] - bbox.min[2],
    );
    controls.target.set(cx, cy, cz);
    camera.position.set(cx + extent, cy - extent, cz + extent * 0.7);
  } catch (e) {
    console.warn('基底图加载失败:', e);
    statBase.textContent = `基底: 加载失败`;
    // 不抛错,允许页面继续工作(WebSocket 数据仍可用)
  }
}
```

**7) 启动序列(line 240-241)改为**:
```js
loadBaseMap();      // 不 await,与 WebSocket 并行
connect();
requestAnimationFrame(tick);
```

### C.5 `slam.css` 可选补充

在 `.slam-statusbar` 节后新增按钮样式:
```css
.slam-btn {
  margin-left: auto;
  background: rgba(126, 184, 255, 0.15);
  color: #cfe0f5;
  border: 1px solid rgba(126, 184, 255, 0.4);
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
}
.slam-btn:hover { background: rgba(126, 184, 255, 0.25); }
```

### C.6 网格 / 坐标轴 / 相机随地图自适应（2026-06-03 换图后新增）

**背景**：换图后地图跨度约 112m × 95m，而原 `slam.js` 的 `GridHelper(20,20)` 只有 20m、`AxesHelper(1.0)` 仅 1m、相机 far=500、`maxDistance=200`、双击复位写死 `(0,0,15)`，导致大地图点云跑到网格外、看不全、复位也不对。

**改动**（均在 `slam.js`，纯前端，不动协议/后端，**点云真实坐标不变**）：

- **相机**：`far` 500 → **4000**；`controls.maxDistance` 200 → 3000，并在 `loadBaseMap` 里按 `max(maxAbsX,maxAbsY)*6` 进一步放开。
- **网格/坐标轴留在真实原点 (0,0,0)**（和坐标轴合体，不跟随地图中心）：`fitSceneToMap(bbox)` 把 `GridHelper` 尺寸取 `ceil(maxAbs*2/10)*10`（原点居中、覆盖最远点，每格约 10m）、放在原点；`AxesHelper` 缩放到 `max(2, maxAbs*0.1)`。
- **默认视角 & 双击复位 = 坐标系原点 (0,0,0) 正上方俯视**（`applyFittedView()`）：高度按**当前视口纵横比**算出"刚好框住整图 + 10% 余量"的最小值，不会过高；Z-up，复现旧版"正上方"手感。`fittedView` 用 `{center:(0,0,0), xspan:2·maxAbsX, yspan:2·maxAbsY}`，每次双击按当前 aspect 实时重算（含 aspect 非法兜底防 NaN）。原点作固定参考——无论某次建图离原点远近，双击都回到原点正上方。
- **基底图点尺寸改屏幕恒定像素**（`sizeAttenuation:false`，基底 size=2.5px / 补全 size=1.8px）：原本 0.08m + sizeAttenuation 在百米俯视下会缩成亚像素、整图看着像空的；改恒定像素后任意缩放都稳定可见。累积层/scan 层不动（仍 world-size，近距观察要深度）。

**坐标系原点与负 z 说明**：原点 (0,0,0) 是 SLAM 的 `a/camera_init` frame 原点 = 建图/起飞起点，**不是地面**。地图天然"从起点往外长"，所以中心偏离原点是正常的；点云出现 **z<0** 也正常——那是物理上低于传感器起始高度的部分（地面、下坡等），新图 z∈[-3.54, 6.39] 即起点上下约 ±数米。

**注意**：`slam.html` 里 `slam.js` 引用为 `?v=N` 缓存破除，平板 WebView 需确保加载到新版（当前已到 `?v=21`，每改一次 +1）。

---

## §C.7 导航交互大改 — 飞入式缩放 + 平移模式（2026-06-04）

**背景**：§C.6 的"正上方俯视"默认视角在实机上体验很差：①正俯视时单指旋转 = 整张大图绕屏幕中心原地打转，难控制（数学上不是 bug，是 OrbitControls 在视线‖up 时的退化 + 俯视旋转的视觉特性）；②OrbitControls 的缩放本质是"逼近目标点后停住"，**进不了点云内部**（房间外墙就是墙，放大只能贴到外表面）。本节把交互整套调顺，**纯前端、不动协议/后端/渲染**。

**改动（均在 `slam.js`/`slam.html`/`slam.css`）**：

1. **默认/双击视角 = 斜上方俯瞰**（`applyFittedView`）：相机从地图中心的西南上方 `(+d,-d,+d)`（`d=extent*0.7`）看向中心，Z-up。偏移三分量都非零，**避开正俯视的万向锁**，旋转是绕地图 orbit、好控制。`fittedView` 存 `{center, extent}`。

2. **"默认视角"按钮改为保存真实相机姿态**（`setDefViewEditMode`）：不再强制拍平成正俯视、不再锁旋转。点一次进入"自由摆位"（可任意转/平移/缩放），再点一次把当前 `{pos,target,up}` 原样存入 localStorage。存储 key 升到 **`aiagent.slam.defaultView.v2`**（旧 v1 只存"俯视中心+高度"，自动失效→回退斜视默认）。`loadDefaultView` 校验 pos/target/up 三个长度 3 的有限数组。

3. **飞入式缩放 `flyDolly`**（核心）：`controls.enableZoom=false` 关掉自带缩放，改为沿视线**推进整个支点（相机+目标一起走）**——先把相机-目标距离收缩到 `FLY_R_MIN=0.8m`，收缩到底后多余推进量平移支点 = **穿墙进屋**；反向先拉到 `FLY_R_MAX=600m` 再整体后退。进屋后支点就在身前约 0.8m，**屋内旋转依旧平稳**。步长基数钳制在 `[5,100]m`，避免屋外冲太远 / 屋内龟速爬。
   - **PC**：`wheel` 监听 → `flyDolly(±0.15)`。
   - **平板**：`pointerdown/move/up`（pointer 事件、**不 preventDefault**，与 OrbitControls 双指平移并存）跟踪两指，捏合间距变化 → `flyDolly`。

4. **平移模式按钮**（`#btn-panmode` 顶栏 + `.panmode-fab` 左上悬浮，蓝色点亮）：双指"平移 vs 捏合"自动判别在实机上仍有串扰，最终改为**显式模式切换**更可控。`setPanMode(true)`：`touches.ONE=PAN`、`mouseButtons.LEFT=PAN`、`enableRotate=false`、`isPanMode=true`（`flyDolly` 首行 `if(isPanMode)return` 禁飞）→ 单指/左键只平移；再点恢复。fab 定位在**左上 `left:14px`**（embedded 顶栏隐藏，左上空）。

5. 其它：`panSpeed=1.8`、`screenSpacePanning=true`（近距平移更跟手）；`minDistance=0.3` 维持原值（缩放已被飞入式接管）。

**踩过的坑（别再犯）**：
- `controls.touches.TWO` 只接受 `DOLLY_PAN`/`DOLLY_ROTATE`；设成 `THREE.TOUCH.PAN` 会让**双指整个失效**。双指平移就用默认 `DOLLY_PAN`，其 dolly 部分已被 `enableZoom=false` 关掉、只剩 pan。
- 平板手势监听**必须用 pointer 事件且不要 `preventDefault`**；用 `touchmove+preventDefault` 会掐断 OrbitControls 的双指平移。
- 正俯视（视线‖up）是 OrbitControls 万向锁退化点，默认视角**不要**死正俯视，给倾角即可。
- **基底点尺寸**：现行代码是 `size:0.08, sizeAttenuation:true`（世界尺寸，原 §C.4 值）。§C.6 提到的"改恒定像素 `sizeAttenuation:false` 2.5px"**未保留在当前代码中**（曾试图用动态像素解决"放大不进去"，发现真因是 zoomToCursor 吸附到表面、与点尺寸无关，已回退）。以现行 §C.7 为准。

**临时调试钩子**：`window.__slamDbg()` / `__ctl` / `__cam`（暴露控制器与相机，console 可实时调参/读快照）为定位问题所加，**发布前应删除**。

---

## §D. 验收清单

### D.1 P0 任务的"完成判据"

| 任务 | 完成判据 |
|---|---|
| 复制 PCD | `curl -I http://localhost:<port>/static/maps/global_map_ds.pcd` 返回 200,`Content-Length: 342652`(换图后；旧 demo 为 90504) |
| pcd_parser.js | 在 Chrome console: `parsePCD(buf).pointCount === 21404`（旧 demo 5587）且 bbox.min[z] < bbox.max[z] |
| voxel_infill.js | `voxelInfill(parsed.positions, 0.1).length / 3` 在 30000-80000 之间 |
| voxel_accumulator.js | 单测: 同一点 push 100 次 size()==1;push 16 万随机点后 size()<=15万 |
| slam.html SLAM_CONFIG | F12 console `window.SLAM_CONFIG.baseMapFile === 'global_map_ds.pcd'` |
| slam.html 按钮 | `document.getElementById('btn-clear-accum')` 存在 |
| slam.js 基底图层 | 打开 /slam,看到灰蓝色点云(基底图)和它周围更小更透的虚拟点 |
| slam.js 累积重写 | stub 模式下,累积层从空逐渐增长;按"清除累积"瞬间清空 |
| slam.js 累积去重 | stub 模式跑 5 分钟,累积层点数稳定不爆炸 |
| 现有功能不回归 | scan/odom/path 表现与改造前一致(stub 模式肉眼比对) |

### D.2 手动测试步骤(stub 模式)

```bash
cd /home/kian/kian_project/aiagent
AIAGENT_SLAM_STUB=1 python -m src.application   # 或项目实际启动命令
# 浏览器打开 http://localhost:<port>/slam
```

预期看到:
- 灰蓝色基底图轮廓出现(全局地图,5587 真实点 + ~4 万虚拟点)
- 螺旋假数据(累积层)逐渐生长,rainbow 上色
- 橙色小球(scan)绕着原点画圆
- 粉红箭头(位姿)跟着移动
- 绿色折线(轨迹)逐渐拉长
- 状态栏: "基底: 5,587 (+4x,xxx 补全)" "累积: x/150,000" "FPS: 40+"

### D.3 性能验收

| 指标 | 目标 | 测量 |
|---|---|---|
| 加载基底图 | < 500ms | console.time |
| 累积层峰值 FPS | ≥ 40 | 状态栏 FPS |
| 累积达到上限后稳定性 | ±5 FPS 波动 | 跑 5 分钟观察 |
| 内存占用(Chrome DevTools) | < 300MB | Performance Monitor |

### D.4 失败兜底

- PCD fetch 失败 → 控制台 warn + 状态栏"基底: 加载失败" + WebSocket 数据照常显示
- parsePCD 抛错 → 同上
- WebSocket 断 → 现有 2 秒重连不动

---

## §E. DO / DON'T 边界（违反会被人工拒）

### E.1 DO

- ✅ 严格按 §C 的清单和顺序改文件
- ✅ 保留所有现有功能(scan 滑动窗口、odom、path、reconnect、resize、dblclick 重置视角)
- ✅ 用 `CFG.xxx ?? 默认值` 模式读配置,允许 SLAM_CONFIG 缺失键
- ✅ 所有新模块用 ES module export,通过 `/static/...js` import
- ✅ 不破坏现有 CHAN 二进制协议(channel 0x01-0x04 含义不变)
- ✅ 控制台 warn 用 `console.warn`,error 用 `console.error`
- ✅ 不引入新的 npm/CDN 依赖,只用现有 `three` 和原生 Web API

### E.2 DON'T

- ❌ 不要"顺手优化"现有 CHAN 协议、改字节布局
- ❌ 不要给 mapPoints/scanPoints 加 hover/click/labeling 等额外功能
- ❌ 不要实现禁飞区 UI(只写 `utils.js` 的 `screenToWorldXY`,不画框选)
- ❌ 不要改 `slam_bridge.py` / `slam_constants.py` / `web_server.py`
- ❌ 不要把虚拟点(voxelInfill 输出)回写到 mapAccum 或导出
- ❌ 不要在 baseMapTransform=null 时偷偷加坐标系补偿
- ❌ 不要把 height rainbow 改成单色(已确认保留)
- ❌ 不要把 PCD parser 写成"通用全功能"的(只支持 §B.1 所列子集就够)
- ❌ 不要引入 Web Worker(本次点数规模够小)
- ❌ 不要修改 vendor/ 下的 three.js

### E.3 本次范围之外（明确不做）

- 多飞机支持(按 drone_id 分图层)
- 禁飞区绘制 UI、列表管理、持久化
- 后端禁飞区 API、ROS 转发
- 飞机端违规检测
- PCD ascii / binary_compressed 支持
- PCD RGB / normal 字段支持
- Surfel / Splat / TSDF / Gaussian Splatting 渲染
- 累积层 Web Worker
- 坐标系动态补偿
- 多基底图切换 UI

  说明: 多基底图切换 UI 指同一个 `/slam` 页面支持从多个预建 PCD 地图中选择加载,例如不同楼层、不同场地或不同任务区域;当前只固定加载 `global_map_ds.pcd`,所以此项属于远期增强。

---

## §F. 决策日志（"为什么这么做"的备忘）

### F.1 被否决的方案 + 否决原因

| 方案 | 否决原因 |
|---|---|
| Surfel / Splat (E) | 实施成本 ★★★★,本次时间不允许;5 万点用方案 D 已经够 |
| TSDF + Marching Cubes (F) | 实时建 mesh 太重,且累积层是飞机自己的点云,意义不大 |
| Gaussian Splatting (G) | 工程过度,平板 GPU 撑不住 |
| 把累积层从 rainbow 改单色冷灰 | 失去高度信息,室内多楼层场景区分度差;rainbow 是 rviz 默认习惯 |
| 时间累积(旧)继续用 | 静止时点数爆炸已被验证 |
| 纯 FIFO 数量累积 | 快速移动时挤掉历史,体素去重更优 |
| 静态坐标补偿 baseMapTransform | 偏移方向随机,补反了更糟 |
| 用 PCDLoader from three/examples | 引入新依赖且功能过多;自己写更可控 |
| Web Worker 跑 parsePCD/voxelInfill | 5587 点 + 4 万虚拟点 < 10ms,主线程足够 |

### F.2 已确认的设计选择

| 选择 | 来源 |
|---|---|
| 渲染方案 A+B+C+D | 用户 2026-05-17 对话拍板 |
| 累积体素 0.05m / 上限 15 万 | 同上 |
| 配色: 基底冷灰单色 + 累积 rainbow 保留 | 综合 v3 + 现有代码现状 |
| 禁飞区 MVP: XY 矩形 + 高度 slider | 用户认可,本次只留接口 |
| `baseMapTransform = null` | 队友确认误差方向随机 |
| 不动 web_server.py | `/static` 已通配 web_static/ |

### F.3 已知的妥协（**可能在 v5 优化**）

- 暂不用 disc sprite 材质(方案 B):减少首次实施复杂度,纯方点先跑通,效果不满意再加
- voxel_infill 只补 26 邻居:可以扩展到 124 邻居(±2 voxel),但本次先简单的
- accumulator 重建 Float32Array 每次都是全量:可以做增量(append-only buffer + offset),但 15 万点全量也才 1.8MB / 帧,可接受
- statBase 状态栏文字偏长:不调 CSS,先看实际效果

---

## §G. 离线测试方案（无 ROS / 无飞机数据）

### G.1 用现有 stub 模式

`slam_bridge.py` 已实现 stub。设置环境变量:
```bash
export AIAGENT_SLAM_STUB=1
```
启动后端会推假数据(螺旋地图 + 圆轨迹 scan + odom + path)。前端基底图 + 累积层 + scan + odom + path 全都能看到效果。

### G.2 只测 PCD 加载,不连后端

打开浏览器直接访问 `http://localhost:<port>/slam`,WebSocket 会一直显示 OFFLINE 重试,但 `loadBaseMap()` 与 WebSocket 并行,基底图仍会加载并显示。状态栏其他项 "-" 是正常的。

### G.3 PCD 单元测试(命令行)

不强制要求,但建议在 Chrome console 跑一次:
```js
const r = await fetch('/static/maps/global_map_ds.pcd');
const buf = await r.arrayBuffer();
const { parsePCD } = await import('/static/pcd_parser.js');
const p = parsePCD(buf);
console.assert(p.pointCount === 5587, 'pointCount 应为 5587');
console.assert(p.positions.length === 5587 * 3, 'positions 长度错');
console.assert(p.intensities !== null && p.intensities.length === 5587, 'intensities 错');
console.log('bbox:', p.bbox);
```

### G.4 voxel_accumulator 简易自测

```js
const { VoxelAccumulator } = await import('/static/voxel_accumulator.js');
const a = new VoxelAccumulator(0.05, 100);
const same = new Float32Array([0,0,0, 0.001,0.001,0.001, 0.02,0.02,0.02]);
for (let i = 0; i < 100; i++) a.addBatch(same);
console.assert(a.size() === 1, '同一 voxel 内点应去重为 1');

a.clear();
const rng = (n) => { const o = new Float32Array(n*3); for (let i=0;i<n*3;i++) o[i] = Math.random()*100; return o; };
a.addBatch(rng(200000));
console.assert(a.size() <= 100, '应触发 eviction');
```

---

## §H. P0 待办（带验收引用）

> 按顺序执行,每项做完对照 §D 验收。

- [x] **H1**. `mkdir -p src/display/web_static/maps && cp` PCD 文件 → §D.1 第 1 行 ✅ 2026-05-18
- [x] **H2**. 写 `pcd_parser.js`(按 §B.1) → §D.1 第 2 行 + §G.3 ✅ Node 自测 pointCount=5587
- [x] **H3**. 写 `voxel_infill.js`(按 §B.2) → §D.1 第 3 行 ✅ Node 自测 54,440 虚拟点
- [x] **H4**. 写 `voxel_accumulator.js`(按 §B.3) → §D.1 第 4 行 + §G.4 ✅ 去重 + eviction 通过
- [x] **H5**. 写 `utils.js`(按 §B.4) → 编译通过即可 ✅
- [x] **H6**. 改 `slam.html`(按 §C.3) → §D.1 第 5-6 行 ✅ SLAM_CONFIG + 状态栏按钮
- [x] **H7**. 改 `slam.js`(按 §C.4) → §D.1 第 7-10 行 ✅ 基底图层 / accumulator / loadBaseMap
- [x] **H8**. 改 `slam.css`(可选,按 §C.5) → 按钮可点 ✅
- [x] **H9**. stub 模式手动验收 → §D.2 ✅ 2026-05-20 开发板/平板侧测试通过
- [x] **H10**. 性能 5 分钟观察 → §D.3 ✅ 2026-05-20 开发板/平板侧测试通过

### H9-H10 验收记录(开发板侧)

```bash
# 在开发板上,工程目录下
AIAGENT_SLAM_STUB=1 python -m src.application   # 或项目实际启动命令
# 平板/浏览器打开 http://<开发板ip>:<port>/slam
```

2026-05-20 用户已在开发板/平板侧完成 H9-H10 验收,结果: **测试通过**。

已覆盖:
- §D.2 stub 模式手动验收
- §D.3 性能 5 分钟观察
- 基底图、累积层、scan、odom、path 显示链路
- 清除累积按钮与累积稳定性观察

### 验收后下一步(P1 起点)

H9-H10 已通过 → 可直接进入 §I.P1(禁飞区前端绘制)。起点已经备好:

1. `window.SLAM_CONFIG.enableNoFlyZoneDraw` — 设 `true` 启用
2. `src/display/web_static/utils.js` 的 `screenToWorldXY(event, camera, canvas, raycaster)` — 屏幕坐标→世界 XY 平面,直接用
3. `slam.js` 顶部已有 `const CFG = window.SLAM_CONFIG || {}`,加新模块时复用

P1/P2 实施记录已合并到本文 §I,后续继续维护单一文档。

---

## §I. 禁飞区 P1/P2/P3 实施状态

> **日期**: 2026-05-20  
> **当前范围**: P1 已实现前端绘制与本地持久化;P2 已搭后端 API + ROS bridge 占位框架。飞控消息契约未确认前不实际 ROS publish。

### I.1 P1 — 禁飞区前端绘制（已落地）

- `/slam` 顶栏和 embedded 平板悬浮入口均有 `禁飞区` 按钮。
- 点击按钮后进入禁飞区面板和俯视浏览模式:
  - 相机强制切到俯视
  - OrbitControls 禁用旋转,单指/左键改为平移,双指保留缩放+平移
  - 可先平移/缩放地图到目标区域
  - 点 `开始框选` 后,下一次 pointerdown / move / up 拖拽才生成矩形
  - 框选完成后自动回到俯视浏览模式
- 选区以半透明红色矩形 + 红色边框显示。
- 右侧面板列出禁飞区:
  - 名称可编辑
  - 可删除单个禁飞区
  - 可清空全部禁飞区
  - `zMin` / `zMax` 高度滑条可编辑
- 数据持久化在浏览器 `localStorage`:
  - key: `aiagent.slam.noFlyZones.v1`

### I.2 禁飞区数据结构

```json
{
  "id": "timestamp-random",
  "name": "禁飞区 1",
  "minX": 0.0,
  "maxX": 1.0,
  "minY": 0.0,
  "maxY": 1.0,
  "zMin": 0.0,
  "zMax": 3.0
}
```

> **2026-06-14 更新 — `zMin/zMax` 仅前端展示，后端规划不再用 z 过滤**：
> 规划演进为终端本地 A*（`kian_global_planner.py`）后，飞机只在固定 `planning_z` 单一高度飞，
> 2D A* 对 z 没有实际约束意义。原先 `_active_no_fly_zones` 会「只保留 z 区间覆盖 planning_z 的禁区」，
> 导致用户画的禁区可能因高度对不上而**悄悄失效**（隐患）。现已去掉该过滤——
> **所有框选禁区一律按 xy 平面生效**；`zMin/zMax` 保留给前端画 3D 盒子展示，后端不据此筛选。
> 详见 [`camera_stream_toggle_design.md`](camera_stream_toggle_design.md) 同批改动记录。

### I.3 P2 — 禁飞区下发（框架已落地）

- 前端面板增加 `下发` 按钮。
- `POST /api/noflyzone` 接收当前 localStorage 禁飞区。
- `GET /api/noflyzone` 返回后端最近一次收到的禁飞区。
- 后端校验并归一化 `minX/maxX/minY/maxY/zMin/zMax`。
- `src/ros/nofly_zone_bridge.py` 已作为 ROS 下发边界占位。
- 当前不会实际 publish 到 `/a/no_fly_zones`,等待飞控确认消息类型和字段。

### I.4 P2 剩余待确认

- 实际 ROS publisher 创建与消息发布
- `/a/no_fly_zones` 消息格式
- 是否使用 `geometry_msgs/PolygonStamped[]` 或自定义 msg
- frame_id 是否继续固定为 `a/camera_init`
- 飞控是否需要 z_min/z_max 独立字段,还是 3D polygon / prism 表示

### I.5 P3 — 长期增强（未开始）

- 飞机端违规实时检测 + UI 高亮违规区。
- 多飞机支持(drone_id 分图层、配色、独立累积)。
- 多基底图切换 UI。

  说明: 多基底图切换 UI 指同一个 `/slam` 页面支持选择不同预建 PCD 地图,例如不同楼层、不同场地或不同任务区域。当前仍固定加载 `global_map_ds.pcd`,属于 P3 远期增强。

---

## §J. 风险表

| 风险 | 概率 | 影响 | 处理 |
|---|---|---|---|
| 10cm 残差视觉错位 | 已确认会发生 | 低(点尺寸覆盖) | 接受,不补偿 |
| 残差方向随机 | 已确认 | 中(不能静态补偿) | 等飞控/SLAM 端 |
| 累积层接近上限时 GC 抖动 | 中 | 中 | chunked eviction(§B.3) |
| PCD 换文件后字段不一致 | 低(用户说同格式) | 低 | parser 按 header 自适应 |
| 体素去重 hash 冲突 | 极低 | 低 | 字符串 key,无冲突 |
| 平板 WebView WebGL 老 | 中 | 中 | `lowPerfMode` 兜底 |
| 飞机静止数据污染累积层 | 高 | 低 | 起飞前点"清除累积" |
| basemap 加载与 ws 抢带宽 | 低(88KB 小) | 低 | fetch + ws 并行,不互相 await |

---

## 文档变更日志

- **v1**(初稿): 四层叠加架构 + ABCD 渲染优化 + 配色 + PCD parser 接口
- **v2**: 加性能预算 + 禁飞区分阶段 + 风险表
- **v3**: 坐标系对齐技术讲解 + 时间累积 vs 数量累积对比 + 决策为体素去重 + 低色差配色
- **v4**(本版本): **AI 可执行版** — 增加 §A 现有代码摘录、§B 完整骨架、§C 修改 patch、§D 验收清单、§E DO/DON'T、§F 决策日志、§G 离线测试;修正配色决策(保留累积层 rainbow)
- **v4.1**(2026-05-18 进度标记): H1-H8 代码改动已落地(PC 端);头部加"当前进度速查";§H 勾选 H1-H8、补充 H9-H10 开发板操作提示;**不改设计本体**,只标进度。后续 P1 另开新文档。
- **v4.2**(2026-05-20 验收标记): H9-H10 开发板/平板侧测试通过;P0 H1-H10 全部完成;同步 SLAM topic 为 `/a/drone_0_cloud_registered_world`、`/a/drone_0_Odometry_world`、`/a/path_world`。
- **v4.3**(2026-06-03 换图): `global_map_ds.pcd` 换为新场地数据(21404 点 / 342,652 字节,格式坐标系不变);`voxelInfillSize` 0.1→0.2(虚拟点 24.6w→7.7w 回到预算内,**真实点云不变**);新增 §C.6:网格/坐标轴留原点放大、相机 far/maxDistance 放开、默认与双击=原点正上方俯视(按视口比例框图)、基底图点改屏幕恒定像素(修大地图俯视点缩成亚像素)、补充原点/负 z 说明;旧文档 `background01-04.md` / `FIRST_RESPONSE_LATENCY.md` 归纳为 `VOICE_LATENCY_OPTIMIZATION.md`,`slam_nofly_zone_design.md` 内容已并入本文 §I 故删除。`slam.js?v=5`。

---

**最终对齐确认**: P0 已验收完成。下一阶段可进入 §I.P1 禁飞区前端绘制;任何超出 §I.P1 范围的修改、任何违反 §E 的行为,都要回到用户处确认。
