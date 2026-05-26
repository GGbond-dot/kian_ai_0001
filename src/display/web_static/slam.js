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

const camera = new THREE.PerspectiveCamera(60, 1, 0.05, 500);
camera.position.set(8, -8, 6);
camera.up.set(0, 0, 1);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.zoomToCursor = true;
controls.zoomSpeed = 1.2;
controls.minDistance = 0.3;
controls.maxDistance = 200;

scene.add(new THREE.AxesHelper(1.0));
const grid = new THREE.GridHelper(20, 20, 0x224466, 0x142238);
grid.rotation.x = Math.PI / 2; // GridHelper 默认在 XZ 平面，转到 XY
scene.add(grid);

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
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      color: 0xff4444,
      opacity: preview ? 0.16 : 0.24,
      transparent: true,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  const line = new THREE.Line(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({
      color: preview ? 0xffaaaa : 0xff6666,
      transparent: true,
      opacity: preview ? 0.95 : 0.85,
    }),
  );
  group.add(mesh, line);
  group.userData.mesh = mesh;
  group.userData.line = line;
  group.userData.zoneId = zone.id;
  updateNoFlyObject(group, zone);
  return group;
}

function updateNoFlyObject(group, zone) {
  const minX = Math.min(zone.minX, zone.maxX);
  const maxX = Math.max(zone.minX, zone.maxX);
  const minY = Math.min(zone.minY, zone.maxY);
  const maxY = Math.max(zone.minY, zone.maxY);
  const width = Math.max(NOFLY_MIN_SIZE, maxX - minX);
  const height = Math.max(NOFLY_MIN_SIZE, maxY - minY);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const z = 0.035;

  const mesh = group.userData.mesh;
  mesh.position.set(cx, cy, z);
  mesh.scale.set(width, height, 1);

  const points = new Float32Array([
    minX, minY, z + 0.005,
    maxX, minY, z + 0.005,
    maxX, maxY, z + 0.005,
    minX, maxY, z + 0.005,
    minX, minY, z + 0.005,
  ]);
  const line = group.userData.line;
  line.geometry.setAttribute('position', new THREE.BufferAttribute(points, 3));
  line.geometry.computeBoundingSphere();
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
    saveNoFlyZones();
  });
  range.addEventListener('change', renderNoFlyList);

  row.append(label, range, value);
  return row;
}

function setNoFlyPanelOpen(active) {
  if (CFG.enableNoFlyZoneDraw === false) return;
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
    if (savedView) {
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
  camera.position.set(0, 0, 15);
  controls.target.set(0, 0, 0);
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
  }
}

loadBaseMap();
connect();
requestAnimationFrame(tick);
