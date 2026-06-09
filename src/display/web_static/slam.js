/**
 * SLAM Viewer — three.js 客户端
 *
 * 二进制协议 (与 src/display/slam_bridge.py 一致):
 *   channel 0x01 MAP   : [u8][u32 N][N*3*float32 XYZ]
 *   channel 0x02 SCAN  : [u8][u32 N][N*3*float32 XYZ]
 *   channel 0x03 ODOM  : [u8][7*float32 x y z qx qy qz qw]
 *   channel 0x04 PATH  : [u8][u32 N][N*3*float32 XYZ]
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { parsePCD } from '/static/pcd_parser.js';
import { voxelInfill } from '/static/voxel_infill.js';
import { VoxelAccumulator } from '/static/voxel_accumulator.js';
import { screenToWorldXY } from '/static/utils.js';

const CFG = window.SLAM_CONFIG || {};
const CHAN = { MAP: 0x01, SCAN: 0x02, ODOM: 0x03, PATH: 0x04 };

// ===================== three.js 场景 =====================
const canvas = document.getElementById('slam-canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x06080c);

const camera = new THREE.PerspectiveCamera(60, 1, 0.05, 4000);
camera.position.set(8, -8, 6);
camera.up.set(0, 0, 1);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.enableZoom = false;   // 自带"逼近目标点"的缩放过不了墙，改用下面的 flyDolly 飞入式缩放
controls.minDistance = 0.3;
controls.maxDistance = 3000;
controls.panSpeed = 1.8;       // 右键平移提速（透视下平移量本就随距离缩小，近距太迟钝）
controls.screenSpacePanning = true;  // 平移按屏幕上下左右走，更跟手
// 双指保持默认 DOLLY_PAN：enableZoom=false 已关掉其 dolly 部分，只剩平移；
// 前后飞由下面的捏合监听器负责（TOUCH.PAN 是给单指的，设到 TWO 会让双指失效）

// ===== 飞入式缩放：放大=沿视线向前推进整个支点(相机+目标一起走)，能穿墙进屋再退出 =====
const FLY_R_MIN = 0.8;     // 相机-目标最近距离；收缩到此后多余推进量平移支点(穿墙)
const FLY_R_MAX = 600;     // 最远；拉到此后继续缩小则整体后退
const _flyFwd = new THREE.Vector3();
let isPanMode = false;     // 平移模式：只拖动平移，关旋转与飞行(见底部按钮)
function flyDolly(delta) {  // delta>0 = 往里飞(放大)，<0 = 往外退(缩小)
  if (isPanMode) return;   // 平移模式下禁飞
  _flyFwd.subVectors(controls.target, camera.position);
  const radius = _flyFwd.length();
  if (radius < 1e-6) return;
  _flyFwd.multiplyScalar(1 / radius);
  // 步长基数钳制在 [5,100]m：屋外不会一步冲太远，飞进小屋后也不会变龟速爬
  const step = delta * Math.min(100, Math.max(5, radius));
  const newRadius = Math.min(FLY_R_MAX, Math.max(FLY_R_MIN, radius - step));
  const remain = step - (radius - newRadius);              // 半径吸收不掉的溢出量 → 平移支点
  camera.position.copy(controls.target).addScaledVector(_flyFwd, -newRadius);
  if (remain !== 0) {
    controls.target.addScaledVector(_flyFwd, remain);
    camera.position.addScaledVector(_flyFwd, remain);
  }
  controls.update();
}

// PC 滚轮
canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  flyDolly(e.deltaY < 0 ? 0.15 : -0.15);
}, { passive: false });

// 平板双指捏合（用 pointer 事件、不 preventDefault，与 OrbitControls 双指平移并存：
// 捏合距离变化=前后飞，双指拖动=平移由 OrbitControls 处理）
const _flyPtrs = new Map();
let _pinchPrev = null;   // {dist, cx, cy}
const _pinchState = () => {
  const [a, b] = [..._flyPtrs.values()];
  return {
    dist: Math.hypot(a.x - b.x, a.y - b.y),
    cx: (a.x + b.x) / 2,
    cy: (a.y + b.y) / 2,
  };
};
canvas.addEventListener('pointerdown', (e) => {
  if (e.pointerType !== 'touch') return;
  _flyPtrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (_flyPtrs.size === 2) _pinchPrev = _pinchState();
});
canvas.addEventListener('pointermove', (e) => {
  if (e.pointerType !== 'touch' || !_flyPtrs.has(e.pointerId)) return;
  _flyPtrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (_flyPtrs.size !== 2) return;
  const s = _pinchState();
  if (_pinchPrev) {
    const dSep = s.dist - _pinchPrev.dist;                                  // 两指间距变化=捏合量
    const dCen = Math.hypot(s.cx - _pinchPrev.cx, s.cy - _pinchPrev.cy);    // 两指中点移动=平移量
    // 捏合明显占主导才飞；否则当作平移(交给 OrbitControls)，避免拖动平移时串入飞行
    if (Math.abs(dSep) > dCen) flyDolly(dSep / _pinchPrev.dist);
  }
  _pinchPrev = s;
});
const _flyPtrEnd = (e) => {
  _flyPtrs.delete(e.pointerId);
  if (_flyPtrs.size < 2) _pinchPrev = null;
};
canvas.addEventListener('pointerup', _flyPtrEnd);
canvas.addEventListener('pointercancel', _flyPtrEnd);

// 临时调试钩子：console 里 __ctl=控制器 / __cam=相机，可实时改参数；__slamDbg() 读快照
window.__ctl = controls;
window.__cam = camera;
window.__slamDbg = () => ({
  rotateSpeed: controls.rotateSpeed,
  panSpeed: controls.panSpeed,
  zoomToCursor: controls.zoomToCursor,
  screenSpacePanning: controls.screenSpacePanning,
  minDistance: controls.minDistance,
  maxDistance: +controls.maxDistance.toFixed(1),
  near: camera.near,
  far: camera.far,
  up: camera.up.toArray(),
  pos: camera.position.toArray().map(n => +n.toFixed(2)),
  target: controls.target.toArray().map(n => +n.toFixed(2)),
  dist: +camera.position.distanceTo(controls.target).toFixed(3),
});

const axes = new THREE.AxesHelper(2.0);
scene.add(axes);

// 网格初始 20m，加载基底图后按 bbox 重建（见 fitSceneToMap）
let grid = makeGrid(20, 20, 0, 0, 0);
scene.add(grid);
// {center:Vector3, xspan, yspan}；基底图加载后记录，供初始/双击的正上方俯视
let fittedView = null;

function makeGrid(size, divisions, cx, cy, z) {
  const g = new THREE.GridHelper(size, divisions, 0x224466, 0x142238);
  g.rotation.x = Math.PI / 2; // GridHelper 默认在 XZ 平面，转到 XY
  g.position.set(cx, cy, z);
  return g;
}

// 网格与坐标轴一起留在真实原点 (0,0,0)，只是放大到能覆盖整张地图
function fitSceneToMap(bbox) {
  const maxAbs = Math.max(
    Math.abs(bbox.min[0]), Math.abs(bbox.max[0]),
    Math.abs(bbox.min[1]), Math.abs(bbox.max[1]),
  );
  const size = Math.max(20, Math.ceil((maxAbs * 2) / 10) * 10); // 原点居中、覆盖最远点
  const divisions = Math.max(10, Math.round(size / 10));        // 每格约 10m
  scene.remove(grid);
  grid.geometry.dispose();
  grid.material.dispose();
  grid = makeGrid(size, divisions, 0, 0, 0);
  scene.add(grid);
  axes.scale.setScalar(Math.max(2, maxAbs * 0.1));
}

// 用户没自定义"默认视角"时的回退俯视高度（米）
const DBLCLICK_VIEW_HEIGHT = 40;
// 用户自定义默认视角（保存真实相机姿态：位置 + 目标 + up）持久化
// v2 起改为存完整姿态，旧 v1（只存俯视中心+高度）自动失效、回退到斜视默认
const DEFVIEW_STORAGE_KEY = 'aiagent.slam.defaultView.v2';

function loadDefaultView() {
  try {
    const raw = localStorage.getItem(DEFVIEW_STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    const ok = (a) => Array.isArray(a) && a.length === 3 && a.every(Number.isFinite);
    if (ok(v.pos) && ok(v.target) && ok(v.up)) return v;
  } catch (e) { console.warn('默认视角读取失败:', e); }
  return null;
}

// 双击/初始复位：优先用用户存的"默认视角"，否则回退到地图正上方固定高度
function applyFittedView() {
  const def = loadDefaultView();
  if (def) {
    camera.up.fromArray(def.up);
    controls.target.fromArray(def.target);
    camera.position.fromArray(def.pos);
    controls.update();
    return;
  }
  if (!fittedView) return;
  const c = fittedView.center;
  // 斜上方俯瞰（不是正死俯视）：相机从西南上方看向地图中心。
  // 偏移向量三个分量都非零，既避开了正俯视的万向锁，旋转又是绕地图orbit，
  // 单指拖动手感跟换图前一致、好控制。
  const d = (fittedView.extent || DBLCLICK_VIEW_HEIGHT) * 0.7;
  camera.up.set(0, 0, 1);                 // Z-up，世界坐标自然朝向
  controls.target.copy(c);
  camera.position.set(c.x + d, c.y - d, c.z + d);
  camera.lookAt(c);
  controls.update();
}

// "默认视角"编辑模式：点一次进入"自由摆位"，可任意旋转/平移/缩放摆好角度，
// 再点一次把当前真实相机姿态(位置+目标+up)存为默认。不再强制正俯视、不锁旋转。
let isDefViewEdit = false;

function updateDefViewButtons(active) {
  for (const b of [btnDefView, btnDefViewFab]) {
    if (!b) continue;
    b.classList.toggle('active', active);
    b.textContent = active ? '✓ 保存默认视角' : '默认视角';
  }
}

function setDefViewEditMode(active) {
  isDefViewEdit = active;
  updateDefViewButtons(active);
  if (active) return;                              // 进入只是切按钮文案 + 抑制双击复位
  // 退出 = 保存当前真实相机姿态，原样还原（含倾角），从这个角度旋转就是自然 orbit
  const def = {
    pos: camera.position.toArray(),
    target: controls.target.toArray(),
    up: camera.up.toArray(),
  };
  try { localStorage.setItem(DEFVIEW_STORAGE_KEY, JSON.stringify(def)); }
  catch (e) { console.warn('默认视角保存失败:', e); }
}

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

// ===== 累积地图点云 =====
const mapGeom = new THREE.BufferGeometry();
mapGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
mapGeom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(0), 3));
const mapMat = new THREE.PointsMaterial({
  size: 0.07, vertexColors: true, sizeAttenuation: true,
});
const mapPoints = new THREE.Points(mapGeom, mapMat);
scene.add(mapPoints);

const mapAccum = new VoxelAccumulator(
  CFG.accumulateVoxelSize ?? 0.05,
  CFG.accumulateMaxPoints ?? 150000,
);

// ===== 实时 scan =====
const scanGeom = new THREE.BufferGeometry();
scanGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const scanMat = new THREE.PointsMaterial({
  size: 0.07, color: 0xffaa33, sizeAttenuation: true,
});
const scanPoints = new THREE.Points(scanGeom, scanMat);
scene.add(scanPoints);

// scan 滑动窗口: 累积最近 SCAN_WINDOW_FRAMES 帧叠加显示,
// 让 5Hz 推送在视觉上不闪烁,且密度 ≈ 单帧 × N (带宽不变)
// 5Hz × 5s = 25 帧;若觉得拖影太长改小,觉得稀疏改大
const SCAN_WINDOW_FRAMES = 25;
const scanFrames = [];

// ===== Path =====
const pathGeom = new THREE.BufferGeometry();
pathGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const pathMat = new THREE.LineBasicMaterial({ color: 0x33ff88 });
const pathLine = new THREE.Line(pathGeom, pathMat);
scene.add(pathLine);

// ===== 当前位姿 (箭头) =====
const poseArrow = new THREE.ArrowHelper(
  new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0),
  0.6, 0xff4477, 0.18, 0.1,
);
scene.add(poseArrow);

// ===== 禁飞区 (P1: 前端绘制 + localStorage) =====
const noFlyGroup = new THREE.Group();
scene.add(noFlyGroup);

// ===== 抓取目标 (P1: 框选 → 后端塌缩成中心点) =====
const graspGroup = new THREE.Group();
scene.add(graspGroup);

// ===================== 状态条 =====================
const statBase = document.getElementById('stat-base');
const statMap  = document.getElementById('stat-map');
const statScan = document.getElementById('stat-scan');
const statPose = document.getElementById('stat-pose');
const statFps  = document.getElementById('stat-fps');
const connBadge = document.querySelector('.conn-badge');
const btnClearAccum = document.getElementById('btn-clear-accum');
const btnNoFlyDraw = document.getElementById('btn-nofly-draw');
const btnNoFlyFab = document.getElementById('btn-nofly-fab');
const btnNoFlyClear = document.getElementById('btn-nofly-clear');
const btnNoFlySend = document.getElementById('btn-nofly-send');
const btnNoFlyArm = document.getElementById('btn-nofly-arm');
const noFlyPanel = document.getElementById('nofly-panel');
const noFlyList = document.getElementById('nofly-list');
const btnGraspDraw = document.getElementById('btn-grasp-draw');
const btnGraspFab = document.getElementById('btn-grasp-fab');
const btnGraspArm = document.getElementById('btn-grasp-arm');
const btnGraspSend = document.getElementById('btn-grasp-send');
const btnGraspClear = document.getElementById('btn-grasp-clear');
const graspPanel = document.getElementById('grasp-panel');
const graspInfo = document.getElementById('grasp-info');
const btnDefView = document.getElementById('btn-defview');
const btnDefViewFab = document.getElementById('btn-defview-fab');
const onDefViewClick = () => setDefViewEditMode(!isDefViewEdit);
btnDefView?.addEventListener('click', onDefViewClick);
btnDefViewFab?.addEventListener('click', onDefViewClick);

// 清除默认视角：删掉 localStorage 存档 + 强制 Z-up 复位，回到斜视默认。
// 也是横滑翻滚(camera.up 残留成非 Z 轴)时的复位手段。
const btnDefViewClear = document.getElementById('btn-defview-clear');
const btnDefViewClearFab = document.getElementById('btn-defview-clear-fab');
const onDefViewClear = () => {
  try { localStorage.removeItem(DEFVIEW_STORAGE_KEY); }
  catch (e) { console.warn('清除默认视角失败:', e); }
  isDefViewEdit = false;
  updateDefViewButtons(false);   // 退出编辑态但不触发保存
  camera.up.set(0, 0, 1);        // 强制 Z-up，消除残留翻滚
  applyFittedView();             // 回退到斜视默认（无存档）
};
btnDefViewClear?.addEventListener('click', onDefViewClear);
btnDefViewClearFab?.addEventListener('click', onDefViewClear);

// 平移模式开关：开 = 单指/左键只平移，关旋转与飞行；关 = 恢复转/飞/平移
const btnPanMode = document.getElementById('btn-panmode');
const btnPanModeFab = document.getElementById('btn-panmode-fab');
function setPanMode(active) {
  isPanMode = active;
  controls.enableRotate = !active;
  controls.touches.ONE = active ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
  controls.mouseButtons.LEFT = active ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
  for (const b of [btnPanMode, btnPanModeFab]) b?.classList.toggle('active', active);
}
const onPanModeClick = () => setPanMode(!isPanMode);
btnPanMode?.addEventListener('click', onPanModeClick);
btnPanModeFab?.addEventListener('click', onPanModeClick);
btnClearAccum?.addEventListener('click', () => {
  mapAccum.clear();
  rebuildMapGeom();
});

// ===================== 禁飞区绘制 =====================
const NOFLY_STORAGE_KEY = 'aiagent.slam.noFlyZones.v1';
const NOFLY_DEFAULT_Z_MIN = 0;
const NOFLY_DEFAULT_Z_MAX = 3;
const NOFLY_RANGE_MIN = -2;
const NOFLY_RANGE_MAX = 20;
const NOFLY_RANGE_STEP = 0.1;
const NOFLY_MIN_SIZE = 0.05;

const noFlyRaycaster = new THREE.Raycaster();
const noFlyObjects = new Map();
const noFlyPreview = createNoFlyObject({
  id: 'preview',
  name: 'preview',
  minX: 0,
  maxX: 0,
  minY: 0,
  maxY: 0,
  zMin: NOFLY_DEFAULT_Z_MIN,
  zMax: NOFLY_DEFAULT_Z_MAX,
}, true);
noFlyPreview.visible = false;
scene.add(noFlyPreview);

let noFlyZones = loadNoFlyZones();
let isNoFlyPanelOpen = false;
let isNoFlyDrawMode = false;
let noFlyDragStart = null;
let savedView = null;

function loadNoFlyZones() {
  try {
    const raw = localStorage.getItem(NOFLY_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isValidNoFlyZone) : [];
  } catch (e) {
    console.warn('禁飞区读取失败:', e);
    return [];
  }
}

function isValidNoFlyZone(zone) {
  return zone
    && Number.isFinite(zone.minX) && Number.isFinite(zone.maxX)
    && Number.isFinite(zone.minY) && Number.isFinite(zone.maxY)
    && Number.isFinite(zone.zMin) && Number.isFinite(zone.zMax);
}

function saveNoFlyZones() {
  try {
    localStorage.setItem(NOFLY_STORAGE_KEY, JSON.stringify(noFlyZones));
  } catch (e) {
    console.warn('禁飞区保存失败:', e);
  }
}

async function sendNoFlyZones() {
  if (!btnNoFlySend) return;
  const oldText = btnNoFlySend.textContent;
  btnNoFlySend.disabled = true;
  btnNoFlySend.textContent = "下发中";
  try {
    const resp = await fetch("/api/noflyzone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "slam_web",
        frame_id: "a/camera_init",
        zones: noFlyZones,
      }),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
    btnNoFlySend.textContent = data.publish?.published ? "已下发" : "已保存";
    setTimeout(() => { btnNoFlySend.textContent = oldText; }, 1200);
  } catch (e) {
    console.warn("禁飞区下发失败:", e);
    btnNoFlySend.textContent = "失败";
    setTimeout(() => { btnNoFlySend.textContent = oldText; }, 1600);
  } finally {
    btnNoFlySend.disabled = false;
  }
}

function createNoFlyObject(zone, preview = false) {
  const group = new THREE.Group();
  // 立体禁飞区：半透明盒子（体积）+ 12 条棱线框（轮廓）。
  // 盒子用单位 BoxGeometry，由 updateNoFlyObject 通过 scale 撑成实际 长×宽×高。
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshBasicMaterial({
      color: 0xff4444,
      opacity: preview ? 0.12 : 0.18,
      transparent: true,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
    new THREE.LineBasicMaterial({
      color: preview ? 0xffaaaa : 0xff6666,
      transparent: true,
      opacity: preview ? 0.95 : 0.85,
    }),
  );
  group.add(mesh, edges);
  group.userData.mesh = mesh;
  group.userData.edges = edges;
  group.userData.zoneId = zone.id;
  updateNoFlyObject(group, zone);
  return group;
}

function updateNoFlyObject(group, zone) {
  const minX = Math.min(zone.minX, zone.maxX);
  const maxX = Math.max(zone.minX, zone.maxX);
  const minY = Math.min(zone.minY, zone.maxY);
  const maxY = Math.max(zone.minY, zone.maxY);
  // 高度方向（THREE z 轴 = 世界高度），由禁飞区的 zMin/zMax 决定盒子的厚度与垂直位置。
  const zMin = Math.min(zone.zMin, zone.zMax);
  const zMax = Math.max(zone.zMin, zone.zMax);
  const width = Math.max(NOFLY_MIN_SIZE, maxX - minX);
  const depth = Math.max(NOFLY_MIN_SIZE, maxY - minY);
  const tall = Math.max(NOFLY_MIN_SIZE, zMax - zMin);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const cz = (zMin + zMax) / 2;

  const mesh = group.userData.mesh;
  mesh.position.set(cx, cy, cz);
  mesh.scale.set(width, depth, tall);

  const edges = group.userData.edges;
  edges.position.set(cx, cy, cz);
  edges.scale.set(width, depth, tall);
}

function disposeNoFlyObject(group) {
  for (const child of group.children) {
    child.geometry?.dispose();
    child.material?.dispose();
  }
}

function renderNoFlyZones() {
  for (const obj of noFlyObjects.values()) {
    noFlyGroup.remove(obj);
    disposeNoFlyObject(obj);
  }
  noFlyObjects.clear();

  for (const zone of noFlyZones) {
    const obj = createNoFlyObject(zone);
    noFlyObjects.set(zone.id, obj);
    noFlyGroup.add(obj);
  }
  renderNoFlyList();
}

function renderNoFlyList() {
  if (!noFlyList) return;
  noFlyList.replaceChildren();

  if (noFlyZones.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'nofly-empty';
    empty.textContent = '暂无禁飞区';
    noFlyList.appendChild(empty);
    return;
  }

  for (const zone of noFlyZones) {
    const item = document.createElement('div');
    item.className = 'nofly-item';

    const top = document.createElement('div');
    top.className = 'nofly-row';

    const name = document.createElement('input');
    name.className = 'nofly-name';
    name.value = zone.name || '未命名';
    name.addEventListener('input', () => {
      zone.name = name.value.trim() || '未命名';
      saveNoFlyZones();
    });

    const del = document.createElement('button');
    del.className = 'slam-btn nofly-delete';
    del.type = 'button';
    del.textContent = '删除';
    del.addEventListener('click', () => {
      noFlyZones = noFlyZones.filter((z) => z.id !== zone.id);
      saveNoFlyZones();
      renderNoFlyZones();
    });

    top.append(name, del);
    item.appendChild(top);
    item.appendChild(createHeightRow(zone, 'zMin', '下限'));
    item.appendChild(createHeightRow(zone, 'zMax', '上限'));

    const meta = document.createElement('div');
    meta.className = 'nofly-meta';
    meta.textContent = `x ${zone.minX.toFixed(2)}..${zone.maxX.toFixed(2)} / y ${zone.minY.toFixed(2)}..${zone.maxY.toFixed(2)}`;
    item.appendChild(meta);

    noFlyList.appendChild(item);
  }
}

function createHeightRow(zone, key, labelText) {
  const row = document.createElement('label');
  row.className = 'nofly-range';

  const label = document.createElement('span');
  label.textContent = labelText;

  const range = document.createElement('input');
  range.type = 'range';
  range.min = String(NOFLY_RANGE_MIN);
  range.max = String(NOFLY_RANGE_MAX);
  range.step = String(NOFLY_RANGE_STEP);
  range.value = String(zone[key]);

  const value = document.createElement('span');
  value.textContent = `${zone[key].toFixed(1)}m`;

  range.addEventListener('input', () => {
    zone[key] = Number(range.value);
    if (zone.zMin > zone.zMax) {
      if (key === 'zMin') zone.zMax = zone.zMin;
      else zone.zMin = zone.zMax;
    }
    value.textContent = `${zone[key].toFixed(1)}m`;
    // 立体盒子按新高度实时长高/缩短
    const obj = noFlyObjects.get(zone.id);
    if (obj) updateNoFlyObject(obj, zone);
    saveNoFlyZones();
  });
  range.addEventListener('change', renderNoFlyList);

  row.append(label, range, value);
  return row;
}

function setNoFlyPanelOpen(active, keepView = false) {
  if (CFG.enableNoFlyZoneDraw === false) return;
  // 两个绘制面板互斥(共享 savedView/相机), keepView=true 表示把视角让给即将打开的面板
  if (active && isGraspPanelOpen) setGraspPanelOpen(false, true);
  isNoFlyPanelOpen = active;
  btnNoFlyDraw?.classList.toggle('active', active);
  btnNoFlyFab?.classList.toggle('active', active);
  noFlyPanel?.classList.toggle('hidden', !active);

  if (active) {
    if (!savedView) {
      savedView = {
        position: camera.position.clone(),
        target: controls.target.clone(),
        up: camera.up.clone(),
        enableRotate: controls.enableRotate,
        mouseButtons: { ...controls.mouseButtons },
        touches: { ...controls.touches },
      };
    }
    controls.enableRotate = false;
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.touches.ONE = THREE.TOUCH.PAN;
    controls.touches.TWO = THREE.TOUCH.DOLLY_PAN;
    forceTopDownCamera();
  } else {
    setNoFlyDrawMode(false);
    if (!keepView && savedView) {
      camera.up.copy(savedView.up);
      camera.position.copy(savedView.position);
      controls.target.copy(savedView.target);
      controls.enableRotate = savedView.enableRotate;
      controls.mouseButtons = savedView.mouseButtons;
      controls.touches = savedView.touches;
      controls.update();
      savedView = null;
    }
  }
}

function setNoFlyDrawMode(active) {
  if (!isNoFlyPanelOpen && active) setNoFlyPanelOpen(true);
  isNoFlyDrawMode = active;
  btnNoFlyArm?.classList.toggle('active', active);
  if (btnNoFlyArm) btnNoFlyArm.textContent = active ? '取消框选' : '开始框选';
  if (!active) {
    noFlyDragStart = null;
    noFlyPreview.visible = false;
  }
}

function forceTopDownCamera() {
  const target = controls.target.clone();
  const height = Math.max(20, camera.position.distanceTo(target));
  camera.up.set(0, 1, 0);
  camera.position.set(target.x, target.y, target.z + height);
  camera.lookAt(target);
  controls.update();
}

function zoneFromPoints(a, b) {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: `禁飞区 ${noFlyZones.length + 1}`,
    minX: Math.min(a.x, b.x),
    maxX: Math.max(a.x, b.x),
    minY: Math.min(a.y, b.y),
    maxY: Math.max(a.y, b.y),
    zMin: NOFLY_DEFAULT_Z_MIN,
    zMax: NOFLY_DEFAULT_Z_MAX,
  };
}

function pointFromPointerEvent(event) {
  return screenToWorldXY(event, camera, canvas, noFlyRaycaster);
}

function onNoFlyPointerDown(event) {
  if (!isNoFlyDrawMode || event.button !== 0) return;
  const pt = pointFromPointerEvent(event);
  if (!pt) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  canvas.setPointerCapture?.(event.pointerId);
  noFlyDragStart = pt.clone();
  const zone = zoneFromPoints(noFlyDragStart, noFlyDragStart);
  updateNoFlyObject(noFlyPreview, zone);
  noFlyPreview.visible = true;
}

function onNoFlyPointerMove(event) {
  if (!isNoFlyDrawMode || !noFlyDragStart) return;
  const pt = pointFromPointerEvent(event);
  if (!pt) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const zone = zoneFromPoints(noFlyDragStart, pt);
  updateNoFlyObject(noFlyPreview, zone);
}

function onNoFlyPointerUp(event) {
  if (!isNoFlyDrawMode || !noFlyDragStart) return;
  const pt = pointFromPointerEvent(event);
  event.preventDefault();
  event.stopImmediatePropagation();
  canvas.releasePointerCapture?.(event.pointerId);
  noFlyPreview.visible = false;

  if (pt) {
    const zone = zoneFromPoints(noFlyDragStart, pt);
    if (zone.maxX - zone.minX >= NOFLY_MIN_SIZE && zone.maxY - zone.minY >= NOFLY_MIN_SIZE) {
      noFlyZones.push(zone);
      saveNoFlyZones();
      renderNoFlyZones();
    }
  }
  noFlyDragStart = null;
  setNoFlyDrawMode(false);
}

if (CFG.enableNoFlyZoneDraw === false) {
  btnNoFlyDraw?.classList.add('hidden');
  btnNoFlyFab?.classList.add('hidden');
  noFlyPanel?.classList.add('hidden');
} else {
  btnNoFlyDraw?.addEventListener('click', () => setNoFlyPanelOpen(!isNoFlyPanelOpen));
  btnNoFlyFab?.addEventListener('click', () => setNoFlyPanelOpen(!isNoFlyPanelOpen));
  btnNoFlyArm?.addEventListener("click", () => setNoFlyDrawMode(!isNoFlyDrawMode));
  btnNoFlySend?.addEventListener("click", sendNoFlyZones);
  btnNoFlyClear?.addEventListener('click', () => {
    noFlyZones = [];
    saveNoFlyZones();
    renderNoFlyZones();
  });
  canvas.addEventListener('pointerdown', onNoFlyPointerDown, true);
  canvas.addEventListener('pointermove', onNoFlyPointerMove, true);
  canvas.addEventListener('pointerup', onNoFlyPointerUp, true);
  canvas.addEventListener('pointercancel', onNoFlyPointerUp, true);
  renderNoFlyZones();
}

// ===================== 抓取目标绘制 =====================
// 与禁飞区职责一致: 前端框矩形并保存,后端塌缩成中心点 (cx, cy) 下发。
// 只保留一个目标,再次框选覆盖上一个 (设计 §4.2)。
const GRASP_STORAGE_KEY = 'aiagent.slam.graspTask.v1';
const GRASP_MIN_SIZE = 0.05;
const GRASP_DEFAULT_INTERRUPT_MODE = 1;

const graspRaycaster = new THREE.Raycaster();
let graspObject = null;
const graspPreview = createGraspObject(
  { minX: 0, maxX: 0, minY: 0, maxY: 0 },
  true,
);
graspPreview.visible = false;
scene.add(graspPreview);

let graspTask = loadGraspTask();
let isGraspPanelOpen = false;
let isGraspDrawMode = false;
let graspDragStart = null;

function loadGraspTask() {
  try {
    const raw = localStorage.getItem(GRASP_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return isValidGraspTask(parsed) ? parsed : null;
  } catch (e) {
    console.warn('抓取任务读取失败:', e);
    return null;
  }
}

function isValidGraspTask(t) {
  return t
    && Number.isFinite(t.minX) && Number.isFinite(t.maxX)
    && Number.isFinite(t.minY) && Number.isFinite(t.maxY);
}

function saveGraspTask() {
  try {
    if (graspTask) {
      localStorage.setItem(GRASP_STORAGE_KEY, JSON.stringify(graspTask));
    } else {
      localStorage.removeItem(GRASP_STORAGE_KEY);
    }
  } catch (e) {
    console.warn('抓取任务保存失败:', e);
  }
}

async function sendGraspTask() {
  if (!btnGraspSend) return;
  if (!graspTask) {
    const oldText = btnGraspSend.textContent;
    btnGraspSend.textContent = '无目标';
    setTimeout(() => { btnGraspSend.textContent = oldText; }, 1200);
    return;
  }
  const oldText = btnGraspSend.textContent;
  btnGraspSend.disabled = true;
  btnGraspSend.textContent = '下发中';
  try {
    const resp = await fetch('/api/grasp_task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: 'slam_web',
        frame_id: 'world',
        minX: graspTask.minX,
        maxX: graspTask.maxX,
        minY: graspTask.minY,
        maxY: graspTask.maxY,
        interrupt_mode: graspTask.interrupt_mode ?? GRASP_DEFAULT_INTERRUPT_MODE,
      }),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
    btnGraspSend.textContent = data.publish?.published ? '已下发' : '已保存';
    setTimeout(() => { btnGraspSend.textContent = oldText; }, 1200);
  } catch (e) {
    console.warn('抓取任务下发失败:', e);
    btnGraspSend.textContent = '失败';
    setTimeout(() => { btnGraspSend.textContent = oldText; }, 1600);
  } finally {
    btnGraspSend.disabled = false;
  }
}

function createGraspObject(zone, preview = false) {
  const group = new THREE.Group();
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      color: 0x44ff88,
      opacity: preview ? 0.18 : 0.26,
      transparent: true,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  const line = new THREE.Line(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({
      color: preview ? 0xaaffaa : 0x66ffaa,
      transparent: true,
      opacity: preview ? 0.95 : 0.9,
    }),
  );
  // 中心点标记: 黄色小球,提示后端实际下发的就是这个点
  const dot = new THREE.Mesh(
    new THREE.SphereGeometry(0.06, 12, 8),
    new THREE.MeshBasicMaterial({ color: preview ? 0xaaffaa : 0xffe066 }),
  );
  group.add(mesh, line, dot);
  group.userData = { mesh, line, dot };
  updateGraspObject(group, zone);
  return group;
}

function updateGraspObject(group, zone) {
  const minX = Math.min(zone.minX, zone.maxX);
  const maxX = Math.max(zone.minX, zone.maxX);
  const minY = Math.min(zone.minY, zone.maxY);
  const maxY = Math.max(zone.minY, zone.maxY);
  const width = Math.max(GRASP_MIN_SIZE, maxX - minX);
  const height = Math.max(GRASP_MIN_SIZE, maxY - minY);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  // 略高于禁飞区(0.035),避免共面 z-fight
  const z = 0.04;

  const { mesh, line, dot } = group.userData;
  mesh.position.set(cx, cy, z);
  mesh.scale.set(width, height, 1);

  const points = new Float32Array([
    minX, minY, z + 0.005,
    maxX, minY, z + 0.005,
    maxX, maxY, z + 0.005,
    minX, maxY, z + 0.005,
    minX, minY, z + 0.005,
  ]);
  line.geometry.setAttribute('position', new THREE.BufferAttribute(points, 3));
  line.geometry.computeBoundingSphere();

  dot.position.set(cx, cy, z + 0.02);
}

function disposeGraspObject(group) {
  for (const child of group.children) {
    child.geometry?.dispose();
    child.material?.dispose();
  }
}

function renderGraspTask() {
  if (graspObject) {
    graspGroup.remove(graspObject);
    disposeGraspObject(graspObject);
    graspObject = null;
  }
  if (graspTask) {
    graspObject = createGraspObject(graspTask);
    graspGroup.add(graspObject);
  }
  renderGraspInfo();
}

function renderGraspInfo() {
  if (!graspInfo) return;
  if (!graspTask) {
    graspInfo.textContent = '暂无目标,点击"开始框选"在地图上框出抓取区域,后端会取中心点下发。';
    return;
  }
  const cx = (graspTask.minX + graspTask.maxX) / 2;
  const cy = (graspTask.minY + graspTask.maxY) / 2;
  graspInfo.textContent =
    `中心 x=${cx.toFixed(3)}  y=${cy.toFixed(3)}\n` +
    `矩形 x ${graspTask.minX.toFixed(2)}..${graspTask.maxX.toFixed(2)} / y ${graspTask.minY.toFixed(2)}..${graspTask.maxY.toFixed(2)}`;
}

function setGraspPanelOpen(active, keepView = false) {
  if (CFG.enableGraspTask === false) return;
  if (active && isNoFlyPanelOpen) setNoFlyPanelOpen(false, true);
  isGraspPanelOpen = active;
  btnGraspDraw?.classList.toggle('active', active);
  btnGraspFab?.classList.toggle('active', active);
  graspPanel?.classList.toggle('hidden', !active);

  if (active) {
    if (!savedView) {
      savedView = {
        position: camera.position.clone(),
        target: controls.target.clone(),
        up: camera.up.clone(),
        enableRotate: controls.enableRotate,
        mouseButtons: { ...controls.mouseButtons },
        touches: { ...controls.touches },
      };
    }
    controls.enableRotate = false;
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.touches.ONE = THREE.TOUCH.PAN;
    controls.touches.TWO = THREE.TOUCH.DOLLY_PAN;
    forceTopDownCamera();
  } else {
    setGraspDrawMode(false);
    if (!keepView && savedView) {
      camera.up.copy(savedView.up);
      camera.position.copy(savedView.position);
      controls.target.copy(savedView.target);
      controls.enableRotate = savedView.enableRotate;
      controls.mouseButtons = savedView.mouseButtons;
      controls.touches = savedView.touches;
      controls.update();
      savedView = null;
    }
  }
}

function setGraspDrawMode(active) {
  if (!isGraspPanelOpen && active) setGraspPanelOpen(true);
  isGraspDrawMode = active;
  btnGraspArm?.classList.toggle('active', active);
  if (btnGraspArm) btnGraspArm.textContent = active ? '取消框选' : '开始框选';
  if (!active) {
    graspDragStart = null;
    graspPreview.visible = false;
  }
}

function graspZoneFromPoints(a, b) {
  return {
    minX: Math.min(a.x, b.x),
    maxX: Math.max(a.x, b.x),
    minY: Math.min(a.y, b.y),
    maxY: Math.max(a.y, b.y),
    interrupt_mode: graspTask?.interrupt_mode ?? GRASP_DEFAULT_INTERRUPT_MODE,
  };
}

function onGraspPointerDown(event) {
  if (!isGraspDrawMode || event.button !== 0) return;
  const pt = screenToWorldXY(event, camera, canvas, graspRaycaster);
  if (!pt) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  canvas.setPointerCapture?.(event.pointerId);
  graspDragStart = pt.clone();
  const zone = graspZoneFromPoints(graspDragStart, graspDragStart);
  updateGraspObject(graspPreview, zone);
  graspPreview.visible = true;
}

function onGraspPointerMove(event) {
  if (!isGraspDrawMode || !graspDragStart) return;
  const pt = screenToWorldXY(event, camera, canvas, graspRaycaster);
  if (!pt) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const zone = graspZoneFromPoints(graspDragStart, pt);
  updateGraspObject(graspPreview, zone);
}

function onGraspPointerUp(event) {
  if (!isGraspDrawMode || !graspDragStart) return;
  const pt = screenToWorldXY(event, camera, canvas, graspRaycaster);
  event.preventDefault();
  event.stopImmediatePropagation();
  canvas.releasePointerCapture?.(event.pointerId);
  graspPreview.visible = false;

  if (pt) {
    const zone = graspZoneFromPoints(graspDragStart, pt);
    if (zone.maxX - zone.minX >= GRASP_MIN_SIZE && zone.maxY - zone.minY >= GRASP_MIN_SIZE) {
      graspTask = zone;  // 单目标: 覆盖上一个
      saveGraspTask();
      renderGraspTask();
    }
  }
  graspDragStart = null;
  setGraspDrawMode(false);
}

if (CFG.enableGraspTask === false) {
  btnGraspDraw?.classList.add('hidden');
  btnGraspFab?.classList.add('hidden');
  graspPanel?.classList.add('hidden');
} else {
  btnGraspDraw?.addEventListener('click', () => setGraspPanelOpen(!isGraspPanelOpen));
  btnGraspFab?.addEventListener('click', () => setGraspPanelOpen(!isGraspPanelOpen));
  btnGraspArm?.addEventListener('click', () => setGraspDrawMode(!isGraspDrawMode));
  btnGraspSend?.addEventListener('click', sendGraspTask);
  btnGraspClear?.addEventListener('click', () => {
    graspTask = null;
    saveGraspTask();
    renderGraspTask();
  });
  canvas.addEventListener('pointerdown', onGraspPointerDown, true);
  canvas.addEventListener('pointermove', onGraspPointerMove, true);
  canvas.addEventListener('pointerup', onGraspPointerUp, true);
  canvas.addEventListener('pointercancel', onGraspPointerUp, true);
  renderGraspTask();
}

// ===================== 上色: 高度 → rainbow =====================
function colorByHeight(positions, zMin, zMax, out) {
  const span = Math.max(1e-3, zMax - zMin);
  for (let i = 0, j = 0; i < positions.length; i += 3, j += 3) {
    const t = (positions[i + 2] - zMin) / span;
    // 简易 HSV→RGB, H = (1-t)*0.7 (蓝→红)
    const h = (1 - t) * 0.7;
    const c = new THREE.Color().setHSL(h, 1.0, 0.55);
    out[j] = c.r; out[j + 1] = c.g; out[j + 2] = c.b;
  }
}

// ===================== 二进制帧分发 =====================
function onBinary(buf) {
  const dv = new DataView(buf);
  const channel = dv.getUint8(0);

  switch (channel) {
    case CHAN.MAP:  return updateMapAccumulated(buf);
    case CHAN.SCAN: return updateScanWindow(buf);
    case CHAN.ODOM: return updateOdom(dv);
    case CHAN.PATH: return updatePath(buf);
  }
}

function rebuildMapGeom() {
  const xyz = mapAccum.getPositions();
  mapGeom.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
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
  const cap = CFG.accumulateMaxPoints ?? 150000;
  statMap.textContent = `累积: ${mapAccum.size().toLocaleString()}/${cap.toLocaleString()}`;
}

function updateMapAccumulated(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(1, true);
  const xyz = new Float32Array(buf.slice(5, 5 + n * 12));
  mapAccum.addBatch(xyz);
  rebuildMapGeom();
}

function updatePoints(buf, geom, withColor) {
  const dv = new DataView(buf);
  const n = dv.getUint32(1, true);
  // header 5 字节, float32 要求 4 字节对齐, 必须 slice 出新 buffer
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

function updateScanWindow(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(1, true);
  const xyz = new Float32Array(buf.slice(5, 5 + n * 12));

  scanFrames.push(xyz);
  if (scanFrames.length > SCAN_WINDOW_FRAMES) scanFrames.shift();

  let total = 0;
  for (const f of scanFrames) total += f.length;
  const merged = new Float32Array(total);
  let off = 0;
  for (const f of scanFrames) { merged.set(f, off); off += f.length; }

  scanGeom.setAttribute('position', new THREE.BufferAttribute(merged, 3));
  scanGeom.computeBoundingSphere();
  statScan.textContent = `实时点: ${(merged.length / 3).toLocaleString()} (${scanFrames.length}帧窗口)`;
}

function updateOdom(dv) {
  const x  = dv.getFloat32(1,  true);
  const y  = dv.getFloat32(5,  true);
  const z  = dv.getFloat32(9,  true);
  const qx = dv.getFloat32(13, true);
  const qy = dv.getFloat32(17, true);
  const qz = dv.getFloat32(21, true);
  const qw = dv.getFloat32(25, true);

  poseArrow.position.set(x, y, z);
  // 用四元数旋转单位 X 轴得到朝向
  const dir = new THREE.Vector3(1, 0, 0)
    .applyQuaternion(new THREE.Quaternion(qx, qy, qz, qw));
  poseArrow.setDirection(dir);

  const yaw = Math.atan2(2 * (qw * qz + qx * qy),
                         1 - 2 * (qy * qy + qz * qz)) * 180 / Math.PI;
  statPose.textContent = `位姿: x=${x.toFixed(2)} y=${y.toFixed(2)} yaw=${yaw.toFixed(0)}°`;
}

function updatePath(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(1, true);
  const xyz = new Float32Array(buf.slice(5, 5 + n * 12));
  pathGeom.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
  pathGeom.setDrawRange(0, n);
  pathGeom.computeBoundingSphere();
}

// ===================== WebSocket =====================
let ws = null;
let reconnectTimer = null;

function connect() {
  if (ws && ws.readyState <= 1) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/slam`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    clearTimeout(reconnectTimer);
    connBadge.textContent = 'ONLINE';
    connBadge.classList.remove('disconnected');
  };
  ws.onclose = () => {
    connBadge.textContent = 'OFFLINE';
    connBadge.classList.add('disconnected');
    reconnectTimer = setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) onBinary(e.data);
  };
}

// ===================== 渲染循环 =====================
function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w || canvas.height !== h) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
}

let lastFrame = performance.now();
let fpsAcc = 0, fpsCnt = 0;

function tick(now) {
  resize();
  controls.update();
  renderer.render(scene, camera);

  const dt = now - lastFrame;
  lastFrame = now;
  fpsAcc += dt; fpsCnt++;
  if (fpsAcc >= 500) {
    statFps.textContent = `FPS: ${(1000 * fpsCnt / fpsAcc).toFixed(0)}`;
    fpsAcc = 0; fpsCnt = 0;
  }
  requestAnimationFrame(tick);
}

// 双击重置视角
canvas.addEventListener('dblclick', () => {
  if (isDefViewEdit) return;          // 正在设默认视角时不触发复位
  if (fittedView || loadDefaultView()) {
    applyFittedView();                // 回到用户存的默认视角（无则地图正上方）
  } else {
    camera.position.set(0, 0, 15);
    controls.target.set(0, 0, 0);
  }
});

async function loadBaseMap() {
  try {
    const url = `/static/maps/${CFG.baseMapFile || 'global_map_ds.pcd'}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const buf = await resp.arrayBuffer();
    const { positions, pointCount, bbox } = parsePCD(buf);

    baseMapGeom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    baseMapGeom.computeBoundingSphere();

    let infillCount = 0;
    if (CFG.enableVoxelInfill !== false && !CFG.lowPerfMode) {
      const infill = voxelInfill(positions, CFG.voxelInfillSize ?? 0.1);
      baseMapInfillGeom.setAttribute('position', new THREE.BufferAttribute(infill, 3));
      baseMapInfillGeom.computeBoundingSphere();
      infillCount = infill.length / 3;
    }

    statBase.textContent = `基底: ${pointCount.toLocaleString()} (+${infillCount.toLocaleString()}补全)`;

    // 网格/坐标轴留在原点放大；缩放上限按整图放开
    fitSceneToMap(bbox);
    const maxAbsX = Math.max(Math.abs(bbox.min[0]), Math.abs(bbox.max[0]));
    const maxAbsY = Math.max(Math.abs(bbox.min[1]), Math.abs(bbox.max[1]));
    controls.maxDistance = Math.max(controls.maxDistance, Math.max(maxAbsX, maxAbsY) * 6);
    // 默认/双击 = 斜上方俯瞰整张地图（不是正死俯视，正俯视单指拖会让整图原地打转、难控制）
    const cx = (bbox.min[0] + bbox.max[0]) / 2;
    const cy = (bbox.min[1] + bbox.max[1]) / 2;
    const cz = (bbox.min[2] + bbox.max[2]) / 2;
    const extent = Math.max(
      bbox.max[0] - bbox.min[0],
      bbox.max[1] - bbox.min[1],
      bbox.max[2] - bbox.min[2],
    );
    fittedView = { center: new THREE.Vector3(cx, cy, cz), extent };
    applyFittedView();
  } catch (e) {
    console.warn('基底图加载失败:', e);
    statBase.textContent = `基底: 加载失败`;
  }
}

loadBaseMap();
connect();
requestAnimationFrame(tick);
