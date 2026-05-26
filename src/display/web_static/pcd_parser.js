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
  const HEADER_SCAN_BYTES = 2048;
  const headerBytes = new Uint8Array(buf, 0, Math.min(HEADER_SCAN_BYTES, buf.byteLength));
  const headerText = new TextDecoder('ascii').decode(headerBytes);

  // 找到 "DATA xxx\n" 行,行尾是 header 字节结束位置
  const dataLineRe = /^[ \t]*DATA[ \t]+([^\r\n]+)\r?\n/m;
  const m = dataLineRe.exec(headerText);
  if (!m) throw new Error('PCD: 未找到 DATA 行(header 可能 > 2KB)');
  const dataKind = m[1].trim().toLowerCase();
  if (dataKind !== 'binary') {
    throw new Error(`PCD: 仅支持 DATA binary,当前为 ${dataKind}`);
  }
  const headerEnd = m.index + m[0].length;

  // 解析 header 字段
  const fields = parseHeaderField(headerText, 'FIELDS');
  const sizes = parseHeaderField(headerText, 'SIZE').map(Number);
  const types = parseHeaderField(headerText, 'TYPE');
  const counts = parseHeaderField(headerText, 'COUNT').map(Number);
  const widthArr = parseHeaderField(headerText, 'WIDTH').map(Number);
  const heightArr = parseHeaderField(headerText, 'HEIGHT').map(Number);
  const pointsArr = parseHeaderFieldOptional(headerText, 'POINTS');

  if (!fields.length || sizes.length !== fields.length ||
      types.length !== fields.length || counts.length !== fields.length) {
    throw new Error('PCD: FIELDS/SIZE/TYPE/COUNT 长度不一致或缺失');
  }

  // 校验 xyz 字段
  const xIdx = fields.indexOf('x');
  const yIdx = fields.indexOf('y');
  const zIdx = fields.indexOf('z');
  if (xIdx < 0 || yIdx < 0 || zIdx < 0) {
    throw new Error('PCD: 缺少 x/y/z 字段');
  }
  for (const i of [xIdx, yIdx, zIdx]) {
    if (sizes[i] !== 4 || types[i] !== 'F' || counts[i] !== 1) {
      throw new Error(`PCD: 字段 ${fields[i]} 必须为 SIZE=4 TYPE=F COUNT=1`);
    }
  }
  const iIdx = fields.indexOf('intensity');
  if (iIdx >= 0) {
    if (sizes[iIdx] !== 4 || types[iIdx] !== 'F' || counts[iIdx] !== 1) {
      throw new Error('PCD: intensity 字段必须为 SIZE=4 TYPE=F COUNT=1');
    }
  }

  // 计算 stride 和每字段 offset
  let stride = 0;
  const fieldOffsets = new Array(fields.length);
  for (let i = 0; i < fields.length; i++) {
    fieldOffsets[i] = stride;
    stride += sizes[i] * counts[i];
  }
  const xOff = fieldOffsets[xIdx];
  const yOff = fieldOffsets[yIdx];
  const zOff = fieldOffsets[zIdx];
  const iOff = iIdx >= 0 ? fieldOffsets[iIdx] : -1;

  // 点数
  const width = widthArr[0] || 0;
  const height = heightArr[0] || 1;
  const N = pointsArr ? Number(pointsArr[0]) : (width * height);
  if (!Number.isFinite(N) || N <= 0) {
    throw new Error('PCD: 无法确定点数');
  }

  const dataBytes = N * stride;
  if (headerEnd + dataBytes > buf.byteLength) {
    throw new Error(`PCD: 数据段不足 (期望 ${dataBytes} 字节, 实际 ${buf.byteLength - headerEnd})`);
  }

  const dv = new DataView(buf, headerEnd, dataBytes);
  const positions = new Float32Array(N * 3);
  const intensities = iOff >= 0 ? new Float32Array(N) : null;
  let xMin = Infinity, yMin = Infinity, zMin = Infinity;
  let xMax = -Infinity, yMax = -Infinity, zMax = -Infinity;

  for (let p = 0; p < N; p++) {
    const base = p * stride;
    const x = dv.getFloat32(base + xOff, true);
    const y = dv.getFloat32(base + yOff, true);
    const z = dv.getFloat32(base + zOff, true);
    positions[p * 3] = x;
    positions[p * 3 + 1] = y;
    positions[p * 3 + 2] = z;
    if (x < xMin) xMin = x; if (x > xMax) xMax = x;
    if (y < yMin) yMin = y; if (y > yMax) yMax = y;
    if (z < zMin) zMin = z; if (z > zMax) zMax = z;
    if (intensities) intensities[p] = dv.getFloat32(base + iOff, true);
  }

  return {
    positions,
    intensities,
    pointCount: N,
    bbox: { min: [xMin, yMin, zMin], max: [xMax, yMax, zMax] },
  };
}

function parseHeaderField(headerText, name) {
  const re = new RegExp(`^[ \\t]*${name}[ \\t]+([^\\r\\n]+)`, 'm');
  const m = re.exec(headerText);
  if (!m) throw new Error(`PCD: 缺少 header 字段 ${name}`);
  return m[1].trim().split(/\s+/);
}

function parseHeaderFieldOptional(headerText, name) {
  const re = new RegExp(`^[ \\t]*${name}[ \\t]+([^\\r\\n]+)`, 'm');
  const m = re.exec(headerText);
  return m ? m[1].trim().split(/\s+/) : null;
}
