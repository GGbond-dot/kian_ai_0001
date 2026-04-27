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

scene.add(new THREE.AxesHelper(1.0));
const grid = new THREE.GridHelper(20, 20, 0x224466, 0x142238);
grid.rotation.x = Math.PI / 2; // GridHelper 默认在 XZ 平面，转到 XY
scene.add(grid);

// ===== 累积地图点云 =====
const mapGeom = new THREE.BufferGeometry();
mapGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
mapGeom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(0), 3));
const mapMat = new THREE.PointsMaterial({
  size: 0.08, vertexColors: true, sizeAttenuation: true,
});
const mapPoints = new THREE.Points(mapGeom, mapMat);
scene.add(mapPoints);

// ===== 实时 scan =====
const scanGeom = new THREE.BufferGeometry();
scanGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
const scanMat = new THREE.PointsMaterial({
  size: 0.10, color: 0xffaa33, sizeAttenuation: true,
});
const scanPoints = new THREE.Points(scanGeom, scanMat);
scene.add(scanPoints);

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

// ===================== 状态条 =====================
const statMap  = document.getElementById('stat-map');
const statScan = document.getElementById('stat-scan');
const statPose = document.getElementById('stat-pose');
const statFps  = document.getElementById('stat-fps');
const connBadge = document.querySelector('.conn-badge');

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
    case CHAN.MAP:  return updatePoints(buf, mapGeom, true);
    case CHAN.SCAN: return updatePoints(buf, scanGeom, false);
    case CHAN.ODOM: return updateOdom(dv);
    case CHAN.PATH: return updatePath(buf);
  }
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

connect();
requestAnimationFrame(tick);
