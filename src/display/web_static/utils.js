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
