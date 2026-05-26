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
    this._map = new Map();
    this._nextOrder = 0;
    this._dirty = false;
    this._cache = null;
  }

  /**
   * @param {Float32Array} xyz  xyz interleaved
   */
  addBatch(xyz) {
    const V = this.voxelSize;
    for (let i = 0; i < xyz.length; i += 3) {
      const x = xyz[i], y = xyz[i + 1], z = xyz[i + 2];
      const ix = Math.floor(x / V), iy = Math.floor(y / V), iz = Math.floor(z / V);
      const key = `${ix},${iy},${iz}`;
      if (this._map.has(key)) continue;
      this._map.set(key, { x, y, z, order: this._nextOrder++ });
      this._dirty = true;
    }
    if (this._map.size > this.max) {
      this._evictOldest();
      this._dirty = true;
    }
  }

  _evictOldest() {
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
