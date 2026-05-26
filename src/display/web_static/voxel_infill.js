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
 *   - 虚拟点仅供渲染,禁止用于规划/累积/导出
 *   - 输入点数建议 < 5 万,5 万以上请改用 Web Worker(本次不做)
 */

/**
 * @param {Float32Array} positions    xyz interleaved, length = 3*N
 * @param {number} voxelSize          米,默认 0.1
 * @returns {Float32Array}            虚拟点 xyz interleaved
 */
export function voxelInfill(positions, voxelSize = 0.1) {
  const V = voxelSize;
  const realVoxels = new Set();

  for (let i = 0; i < positions.length; i += 3) {
    const ix = Math.floor(positions[i] / V);
    const iy = Math.floor(positions[i + 1] / V);
    const iz = Math.floor(positions[i + 2] / V);
    realVoxels.add(`${ix},${iy},${iz}`);
  }

  const virtualVoxels = new Set();
  for (const key of realVoxels) {
    const [sx, sy, sz] = key.split(',');
    const ix = parseInt(sx, 10), iy = parseInt(sy, 10), iz = parseInt(sz, 10);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        for (let dz = -1; dz <= 1; dz++) {
          if (dx === 0 && dy === 0 && dz === 0) continue;
          const nKey = `${ix + dx},${iy + dy},${iz + dz}`;
          if (!realVoxels.has(nKey)) virtualVoxels.add(nKey);
        }
      }
    }
  }

  const out = new Float32Array(virtualVoxels.size * 3);
  let p = 0;
  for (const key of virtualVoxels) {
    const [sx, sy, sz] = key.split(',');
    const ix = parseInt(sx, 10), iy = parseInt(sy, 10), iz = parseInt(sz, 10);
    out[p++] = (ix + 0.5) * V;
    out[p++] = (iy + 0.5) * V;
    out[p++] = (iz + 0.5) * V;
  }
  return out;
}
