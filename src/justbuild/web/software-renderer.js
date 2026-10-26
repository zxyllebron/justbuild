import * as THREE from 'three';
import { SoftwareRasterizer } from './software-rasterizer.js';

// CPU fallback for reviewing the actual mesh when WebGL is unavailable. This is
// a painter renderer, not an authoritative intersection or CAD validation tool.
const LIGHT = [.3 / Math.hypot(.3, -.5, 1), -.5 / Math.hypot(.3, -.5, 1), 1 / Math.hypot(.3, -.5, 1)];
const SHADES = 64;
const now = () => globalThis.performance?.now() ?? Date.now();
const equal = (a, b) => a && a.length === b.length && b.every((v, i) => a[i] === v);
const copyMatrix = m => Float64Array.from(m.elements);
const clipCode = (x, y, z, w) => (x < -w ? 1 : 0) | (x > w ? 2 : 0) | (y < -w ? 4 : 0) | (y > w ? 8 : 0) | (z < -w ? 16 : 0) | (z > w ? 32 : 0);
const FRUSTUM = [[1, 0, 0, 1], [-1, 0, 0, 1], [0, 1, 0, 1], [0, -1, 0, 1], [0, 0, 1, 1], [0, 0, -1, 1]];

// Shared numeric scratch space clips only boundary triangles. The common path
// projects each indexed vertex once and allocates no per-triangle objects.
function clipPolygon(input, output, count, a, b, c, d) {
  let n = 0;
  for (let i = 0; i < count; i++) {
    const j = (i + 1) % count, p = i * 4, q = j * 4;
    const dp = a * input[p] + b * input[p + 1] + c * input[p + 2] + d * input[p + 3];
    const dq = a * input[q] + b * input[q + 1] + c * input[q + 2] + d * input[q + 3];
    if (dp >= 0) { for (let k = 0; k < 4; k++) output[n * 4 + k] = input[p + k]; n++; }
    if ((dp >= 0) !== (dq >= 0)) {
      const t = dp / (dp - dq);
      for (let k = 0; k < 4; k++) output[n * 4 + k] = input[p + k] + t * (input[q + k] - input[p + k]);
      n++;
    }
  }
  return n;
}

export class SoftwareRenderer {
  constructor({ interactiveTriangleBudget = 30000, interactiveResolutionScale = .75, targetFrameMs = 28, backend = 'auto' } = {}) {
    this.domElement = document.createElement('canvas');
    this.context = this.domElement.getContext('2d');
    if (!this.context) throw new Error('Canvas 2D is unavailable');
    this.width = 1; this.height = 1; this.pixelRatio = 1;
    this.interactiveTriangleBudget = Math.max(1, Math.floor(interactiveTriangleBudget));
    this.interactiveResolutionScale = Math.max(.5, Math.min(.85, Number(interactiveResolutionScale) || .75));
    this.targetFrameMs = Math.max(16, Math.min(50, Number(targetFrameMs) || 28));
    this._previewBudget = this.interactiveTriangleBudget; this._previewScale = this.interactiveResolutionScale; this._renderPixelRatio = 1;
    this._rasterizer = null; this.setBackend(backend);
    this._interactive = false; this._invalidated = true;
    this._geometry = new WeakMap(); this._meshes = new WeakMap();
    this._styleGroups = new Map(); this._styles = [];
    this._vp = new THREE.Matrix4(); this._previousVP = null; this._previousSignature = '';
    this._capacity = 0; this._count = 0; this._grow(1024);
    this._scratchA = new Float64Array(64); this._scratchB = new Float64Array(64);
    this._stats = { quality: 'full', totalTriangles: 0, visibleTriangles: 0, drawnTriangles: 0, frameMs: 0, cacheHits: 0, frameReused: false };
  }
  setPixelRatio(value) { this.pixelRatio = Math.max(.25, Math.min(Number(value) || 1, 1.5)); this._sizeBuffer(); this.invalidate(); }
  setSize(width, height) {
    this.width = Math.max(1, width); this.height = Math.max(1, height);
    this._sizeBuffer();
    this.domElement.style.width = `${this.width}px`; this.domElement.style.height = `${this.height}px`; this.invalidate();
  }
  setClearColor() {}
  _sizeBuffer() {
    const scale = this._interactive && this.backend === 'raster' ? this._previewScale : 1;
    this._renderPixelRatio = this.pixelRatio * scale;
    const width = Math.max(1, Math.round(this.width * this._renderPixelRatio)), height = Math.max(1, Math.round(this.height * this._renderPixelRatio));
    if (this.domElement.width !== width || this.domElement.height !== height) { this.domElement.width = width; this.domElement.height = height; this.invalidate(); }
  }
  setInteractionState(active) {
    if (this._interactive !== !!active) {
      this._interactive = !!active;
      if (active) { this._previewBudget = this.interactiveTriangleBudget; this._previewScale = this.interactiveResolutionScale; }
      this._sizeBuffer(); this.invalidate();
    }
  }
  _adaptPreview(frameMs) {
    if (!this._interactive || !Number.isFinite(frameMs)) return;
    const target = this.targetFrameMs;
    if (frameMs > target * 1.15) {
      this._previewBudget = Math.max(Math.min(12000, this.interactiveTriangleBudget), Math.floor(this._previewBudget * Math.max(.6, Math.sqrt(target / frameMs))));
      this._previewScale = Math.max(.5, this._previewScale - .05);
    } else if (frameMs < target * .6) {
      this._previewBudget = Math.min(this.interactiveTriangleBudget, Math.ceil(this._previewBudget * 1.08));
      this._previewScale = Math.min(this.interactiveResolutionScale, this._previewScale + .025);
    }
  }
  setBackend(backend) {
    if (!['auto', 'canvas', 'raster'].includes(backend)) throw new Error('Unknown software backend');
    const available = typeof this.context.createImageData === 'function' && typeof this.context.putImageData === 'function';
    if (backend === 'raster' && !available) throw new Error('ImageData raster backend is unavailable');
    this.backend = backend === 'canvas' || !available ? 'canvas' : 'raster';
    this._rasterizer = this.backend === 'raster' ? new SoftwareRasterizer(this.context) : null; this._sizeBuffer(); this.invalidate();
  }
  invalidate() { this._invalidated = true; }
  getStats() { return { ...this._stats }; }
  dispose() {
    this._geometry = new WeakMap(); this._meshes = new WeakMap(); this._styleGroups.clear(); this._styles.length = 0;
    this._previousVP = null; this._previousSignature = ''; this._count = 0; if (this.backend === 'raster') this._rasterizer = new SoftwareRasterizer(this.context); this.invalidate();
  }
  _grow(required) {
    if (required <= this._capacity) return;
    const size = Math.max(required, this._capacity * 2, 1024);
    for (const [key, Type, width] of [['_xy', Float32Array, 6], ['_z', Float64Array, 3], ['_flags', Uint8Array, 1], ['_depth', Float64Array, 1], ['_order', Float64Array, 1], ['_style', Uint32Array, 1], ['_alpha', Float32Array, 1], ['_sort', Uint32Array, 1]]) {
      const replacement = new Type(size * width); if (this[key]) replacement.set(this[key]); this[key] = replacement;
    }
    this._capacity = size;
  }
  _geometryData(geometry) {
    const p = geometry.attributes.position, index = geometry.index;
    let data = this._geometry.get(geometry);
    if (data && data.position === p && data.index === index && data.pv === p.version && data.iv === index?.version) return data;
    const local = new Float64Array(p.count * 3);
    for (let i = 0; i < p.count; i++) { local[i * 3] = p.getX(i); local[i * 3 + 1] = p.getY(i); local[i * 3 + 2] = p.getZ(i); }
    const indices = index ? Uint32Array.from({ length: index.count }, (_, i) => index.getX(i)) : null;
    data = { position: p, index, pv: p.version, iv: index?.version, local, indices, count: index ? index.count : p.count };
    this._geometry.set(geometry, data); return data;
  }
  _meshData(mesh, vpChanged) {
    const geometry = this._geometryData(mesh.geometry), m = mesh.matrixWorld.elements;
    let data = this._meshes.get(mesh);
    const changed = !data || data.geometry !== geometry || !equal(data.matrix, m);
    if (!data || data.geometry !== geometry) {
      const n = geometry.local.length / 3;
      data = { geometry, world: new Float64Array(n * 3), projected: new Float64Array(n * 4), codes: new Uint8Array(n), shades: new Uint8Array(Math.floor(geometry.count / 3)), groups: [] };
      this._meshes.set(mesh, data);
    }
    if (changed) {
      const local = geometry.local, world = data.world;
      let minX = Infinity, minY = Infinity, minZ = Infinity, maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (let i = 0; i < local.length; i += 3) {
        const x = local[i], y = local[i + 1], z = local[i + 2];
        const wx = m[0] * x + m[4] * y + m[8] * z + m[12], wy = m[1] * x + m[5] * y + m[9] * z + m[13], wz = m[2] * x + m[6] * y + m[10] * z + m[14];
        world[i] = wx; world[i + 1] = wy; world[i + 2] = wz;
        minX = Math.min(minX, wx); minY = Math.min(minY, wy); minZ = Math.min(minZ, wz); maxX = Math.max(maxX, wx); maxY = Math.max(maxY, wy); maxZ = Math.max(maxZ, wz);
      }
      const ix = geometry.indices;
      for (let t = 0; t < data.shades.length; t++) {
        const k = t * 3, a = (ix ? ix[k] : k) * 3, b = (ix ? ix[k + 1] : k + 1) * 3, c = (ix ? ix[k + 2] : k + 2) * 3;
        const ux = world[b] - world[a], uy = world[b + 1] - world[a + 1], uz = world[b + 2] - world[a + 2], vx = world[c] - world[a], vy = world[c + 1] - world[a + 1], vz = world[c + 2] - world[a + 2];
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx, length = Math.hypot(nx, ny, nz);
        data.shades[t] = Math.round((length ? Math.abs((nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / length) : 0) * (SHADES - 1));
      }
      data.bounds = [minX, minY, minZ, maxX, maxY, maxZ]; data.matrix = copyMatrix(mesh.matrixWorld);
      data.flip = mesh.matrixWorld.determinant() < 0 ? -1 : 1;
    }
    if (changed || vpChanged) {
      const e = this._vp.elements, w = data.world, p = data.projected;
      for (let i = 0, j = 0; i < w.length; i += 3, j += 4) {
        const x = w[i], y = w[i + 1], z = w[i + 2], cw = e[3] * x + e[7] * y + e[11] * z + e[15];
        const cx = e[0] * x + e[4] * y + e[8] * z + e[12], cy = e[1] * x + e[5] * y + e[9] * z + e[13], cz = e[2] * x + e[6] * y + e[10] * z + e[14];
        p[j] = cx / cw; p[j + 1] = cy / cw; p[j + 2] = cz / cw; p[j + 3] = cw; data.codes[j / 4] = clipCode(cx, cy, cz, cw);
      }
    }
    data.changed = changed; return data;
  }
  _outside(bounds) {
    const e = this._vp.elements; let common = 63;
    for (let i = 0; i < 8; i++) {
      const x = bounds[i & 1 ? 3 : 0], y = bounds[i & 2 ? 4 : 1], z = bounds[i & 4 ? 5 : 2];
      common &= clipCode(e[0] * x + e[4] * y + e[8] * z + e[12], e[1] * x + e[5] * y + e[9] * z + e[13], e[2] * x + e[6] * y + e[10] * z + e[14], e[3] * x + e[7] * y + e[11] * z + e[15]);
      if (!common) return false;
    }
    return !!common;
  }
  _colorGroup(material) {
    const color = material.color || new THREE.Color('#ffbd65'), key = `${color.r},${color.g},${color.b}`;
    if (this._styleGroups.has(key)) return this._styleGroups.get(key);
    const offset = this._styles.length, shade = new THREE.Color();
    for (let i = 0; i < SHADES; i++) this._styles.push(shade.copy(color).multiplyScalar(.58 + .42 * i / (SHADES - 1)).getStyle(THREE.SRGBColorSpace));
    this._styleGroups.set(key, offset); return offset;
  }
  _add(x1, y1, z1, x2, y2, z2, x3, y3, z3, group, shade, flip) {
    const area = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1);
    if (!area || !Number.isFinite(area)) { this._stats.zeroProjectedArea++; return; }
    const front = area * flip > 0;
    if ((group.side === THREE.FrontSide && !front) || (group.side === THREE.BackSide && front)) { this._stats.backfaceCulled++; return; }
    const n = this._count; this._grow(n + 1); const o = n * 6;
    // All subpaths use the same winding, so an opaque batch cannot cancel itself.
    this._xy[o] = (x1 + 1) * this.width / 2; this._xy[o + 1] = (1 - y1) * this.height / 2;
    const bx = area > 0 ? x2 : x3, by = area > 0 ? y2 : y3, cx = area > 0 ? x3 : x2, cy = area > 0 ? y3 : y2;
    this._xy[o + 2] = (bx + 1) * this.width / 2; this._xy[o + 3] = (1 - by) * this.height / 2; this._xy[o + 4] = (cx + 1) * this.width / 2; this._xy[o + 5] = (1 - cy) * this.height / 2;
    this._z[n * 3] = z1; this._z[n * 3 + 1] = area > 0 ? z2 : z3; this._z[n * 3 + 2] = area > 0 ? z3 : z2; this._flags[n] = group.flags;
    this._depth[n] = (z1 + z2 + z3) / 3; this._order[n] = group.order; this._style[n] = group.color + shade; this._alpha[n] = group.alpha; this._sort[n] = n; this._count++;
  }
  _boundary(data, a, b, c, planes, group, shade) {
    const needed = (planes.length + 12) * 4;
    if (this._scratchA.length < needed) { this._scratchA = new Float64Array(needed); this._scratchB = new Float64Array(needed); }
    let input = this._scratchA, output = this._scratchB, count = 3;
    for (let i = 0; i < 3; i++) { const source = (i === 0 ? a : i === 1 ? b : c) * 3; input[i * 4] = data.world[source]; input[i * 4 + 1] = data.world[source + 1]; input[i * 4 + 2] = data.world[source + 2]; input[i * 4 + 3] = 1; }
    for (const plane of planes) {
      count = clipPolygon(input, output, count, plane.normal.x, plane.normal.y, plane.normal.z, plane.constant); [input, output] = [output, input]; if (count < 3) return;
    }
    const e = this._vp.elements;
    for (let i = 0; i < count * 4; i += 4) {
      const x = input[i], y = input[i + 1], z = input[i + 2];
      input[i] = e[0] * x + e[4] * y + e[8] * z + e[12]; input[i + 1] = e[1] * x + e[5] * y + e[9] * z + e[13]; input[i + 2] = e[2] * x + e[6] * y + e[10] * z + e[14]; input[i + 3] = e[3] * x + e[7] * y + e[11] * z + e[15];
    }
    for (const plane of FRUSTUM) { count = clipPolygon(input, output, count, ...plane); [input, output] = [output, input]; if (count < 3) return; }
    for (let i = 1; i + 1 < count; i++) {
      const j = i * 4, k = j + 4;
      this._add(input[0] / input[3], input[1] / input[3], input[2] / input[3], input[j] / input[j + 3], input[j + 1] / input[j + 3], input[j + 2] / input[j + 3], input[k] / input[k + 3], input[k + 1] / input[k + 3], input[k + 2] / input[k + 3], group, shade, data.flip);
    }
  }
  render(scene, camera) {
    const started = now(); this._sizeBuffer(); scene.updateMatrixWorld(); camera.updateMatrixWorld();
    this._vp.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const vpChanged = !equal(this._previousVP, this._vp.elements), records = [], signatures = [];
    const stats = { backend: this.backend, rasterPixels: 0, imageDataSubmissions: 0, quality: this._interactive ? 'interactive' : 'full', resolutionScale: this._interactive && this.backend === 'raster' ? this._previewScale : 1, triangleBudget: this._interactive ? this._previewBudget : null, totalTriangles: 0, visibleTriangles: 0, drawnTriangles: 0, sampledTriangles: 0, frameMs: 0, cacheHits: 0, frameReused: false, culledMeshes: 0, backfaceCulled: 0, zeroProjectedArea: 0, canvasFills: 0, visibleMeshes: 0, drawnMeshes: 0 };
    this._stats = stats; let changed = vpChanged;
    scene.traverseVisible(mesh => {
      if (!mesh.isMesh || !mesh.geometry?.attributes.position || !mesh.layers.test(camera.layers)) return;
      const data = this._meshData(mesh, vpChanged); changed ||= data.changed; if (!data.changed) stats.cacheHits++;
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      const groups = Array.isArray(mesh.material) && mesh.geometry.groups.length ? mesh.geometry.groups : [{ start: 0, count: data.geometry.count, materialIndex: 0 }];
      const range = mesh.geometry.drawRange; let shell = false;
      for (let p = mesh; p; p = p.parent) if (p.name === 'base' || p.name === 'lid') { shell = true; break; }
      const outside = this._outside(data.bounds); if (outside) stats.culledMeshes++;
      let groupIndex = 0;
      for (const descriptor of groups) {
        const material = materials[descriptor.materialIndex || 0]; if (!material || !material.visible || material.opacity === 0) continue;
        const start = Math.max(descriptor.start, range.start), end = Math.min(descriptor.start + descriptor.count, range.start + range.count, data.geometry.count), count = Math.max(0, Math.floor((end - start) / 3));
        stats.totalTriangles += count;
        const planes = material.clippingPlanes || [], alpha = material.transparent ? material.opacity : 1, color = this._colorGroup(material);
        const flags = (material.depthTest !== false ? 1 : 0) | (material.depthWrite !== false ? 2 : 0) | (material.transparent ? 4 : 0);
        signatures.push(';', mesh.id, flags, start, end, mesh.renderOrder, alpha, color, material.side, ...planes.flatMap(p => [p.normal.x, p.normal.y, p.normal.z, p.constant]));
        if (outside || !count) continue;
        const group = data.groups[groupIndex] || (data.groups[groupIndex] = {}); groupIndex++;
        Object.assign(group, { data, start, end, count, planes, alpha, color, side: material.side, flags, order: mesh.renderOrder || 0, shell }); records.push(group);
      }
    });
    const signature = signatures.join(',');
    if (!changed && !this._invalidated && signature === this._previousSignature) {
      this._stats = { ...this._lastDrawStats, frameReused: true, canvasFills: 0, imageDataSubmissions: 0, rasterPixels: 0, cacheHits: stats.cacheHits, frameMs: now() - started }; return;
    }
    this._previousSignature = signature; this._previousVP = copyMatrix(this._vp); this._invalidated = false; this._count = 0;
    const flexible = records.reduce((sum, r) => sum + (!r.shell && r.count > 48 ? r.count : 0), 0);
    const fixed = records.reduce((sum, r) => sum + (r.shell || r.count <= 48 ? r.count : 0), 0);
    const ratio = this._interactive ? Math.max(1, flexible / Math.max(1, this._previewBudget - fixed)) : 1;
    const visited = new Set(), drawn = new Set();
    for (const group of records) {
      const { data, planes } = group, ix = data.geometry.indices, p = data.projected, w = data.world, codes = data.codes;
      visited.add(data); const before = this._count;
      const stride = group.shell || group.count <= 48 ? 1 : Math.ceil(ratio);
      for (let k = group.start; k + 2 < group.end; k += 3 * stride) {
        stats.sampledTriangles++;
        const a = ix ? ix[k] : k, b = ix ? ix[k + 1] : k + 1, c = ix ? ix[k + 2] : k + 2;
        if (codes[a] & codes[b] & codes[c]) continue;
        let boundary = !!(codes[a] | codes[b] | codes[c]), rejected = false;
        for (const plane of planes) {
          const n = plane.normal, d = plane.constant, ai = a * 3, bi = b * 3, ci = c * 3;
          const da = n.x * w[ai] + n.y * w[ai + 1] + n.z * w[ai + 2] + d, db = n.x * w[bi] + n.y * w[bi + 1] + n.z * w[bi + 2] + d, dc = n.x * w[ci] + n.y * w[ci + 1] + n.z * w[ci + 2] + d;
          if (da < 0 && db < 0 && dc < 0) { rejected = true; break; } if (da < 0 || db < 0 || dc < 0) boundary = true;
        }
        if (rejected) continue;
        const shade = data.shades[Math.floor(k / 3)];
        if (boundary) this._boundary(data, a, b, c, planes, group, shade);
        else { const ai = a * 4, bi = b * 4, ci = c * 4; this._add(p[ai], p[ai + 1], p[ai + 2], p[bi], p[bi + 1], p[bi + 2], p[ci], p[ci + 1], p[ci + 2], group, shade, data.flip); }
      }
      if (this._count > before) drawn.add(data);
    }
    stats.visibleMeshes = visited.size; stats.drawnMeshes = drawn.size; stats.visibleTriangles = this._count; stats.drawnTriangles = this._count;
    const sorted = this._sort.subarray(0, this._count), depths = this._depth, orders = this._order;
    if (this.backend === 'raster') {
      const flags = this._flags, orderNeedsDepthSort = new Set();
      // The depth buffer makes ordering unnecessary for ordinary opaque layers.
      // Retain prior ordering for unusual layers that disable depth writes/tests.
      for (const i of sorted) if (!(flags[i] & 4) && (flags[i] & 3) !== 3) orderNeedsDepthSort.add(orders[i]);
      sorted.sort((a, b) => orders[a] - orders[b] || (flags[a] & 4) - (flags[b] & 4) || (flags[a] & 4 ? depths[b] - depths[a] : orderNeedsDepthSort.has(orders[a]) ? depths[a] - depths[b] : 0) || a - b);
      stats.rasterPixels = this._rasterizer.draw(this, sorted); stats.imageDataSubmissions = 1; stats.frameMs = now() - started; this._lastDrawStats = { ...stats }; this._adaptPreview(stats.frameMs); return;
    }
    sorted.sort((a, b) => orders[a] - orders[b] || depths[b] - depths[a] || a - b);
    const ctx = this.context, xy = this._xy;
    ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0); ctx.clearRect(0, 0, this.width, this.height);
    let activeStyle = -1, activeAlpha = -1, pending = false;
    for (let j = 0; j < sorted.length; j++) {
      const i = sorted[j], style = this._style[i], alpha = this._alpha[i], o = i * 6;
      if (!pending || style !== activeStyle || alpha !== activeAlpha || alpha < 1) {
        if (pending) { ctx.fill(); stats.canvasFills++; }
        ctx.beginPath(); ctx.fillStyle = this._styles[style]; ctx.globalAlpha = alpha; activeStyle = style; activeAlpha = alpha; pending = true;
      }
      ctx.moveTo(xy[o], xy[o + 1]); ctx.lineTo(xy[o + 2], xy[o + 3]); ctx.lineTo(xy[o + 4], xy[o + 5]); ctx.closePath();
    }
    if (pending) { ctx.fill(); stats.canvasFills++; }
    ctx.globalAlpha = 1; stats.frameMs = now() - started; this._lastDrawStats = { ...stats }; this._adaptPreview(stats.frameMs);
  }
}
