// Pure Node checks: no browser control, network, GUI launch or printer calls.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const moduleURL = pathToFileURL(path.join(root, 'src/justbuild/web/vendor/three.module.js')).href;
const THREE = await import(moduleURL);
let capture = true;
class Context {
  constructor() { this.paths = []; this.current = []; this.fills = 0; this.globalAlpha = 1; }
  setTransform() {}
  clearRect() { this.paths = []; this.fills = 0; }
  beginPath() { this.current = []; }
  moveTo(x, y) { if (capture) this.current.push([[x, y]]); }
  lineTo(x, y) { if (capture) this.current.at(-1).push([x, y]); }
  closePath() {}
  fill() { this.fills++; if (capture) this.paths.push({ color: this.fillStyle, alpha: this.globalAlpha, polygons: this.current }); }
}
globalThis.document = { createElement: () => ({ style: {}, getContext: () => new Context() }) };
const source = (await readFile(path.join(root, 'src/justbuild/web/software-renderer.js'), 'utf8')).replace("from 'three'", `from '${moduleURL}'`).replace("from './software-rasterizer.js'", `from '${pathToFileURL(path.join(root, 'src/justbuild/web/software-rasterizer.js')).href}'`);
const { SoftwareRenderer } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const material = extra => new THREE.MeshBasicMaterial({ color: '#65adf0', side: THREE.DoubleSide, ...extra });
function mesh(vertices, indices = null, mat = material()) {
  const g = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  if (indices) g.setIndex(indices); return new THREE.Mesh(g, mat);
}
function fixture(...objects) {
  const scene = new THREE.Scene(); scene.add(...objects);
  const camera = new THREE.PerspectiveCamera(60, 1, .1, 100); camera.position.set(0, 0, 5); camera.lookAt(0, 0, 0); camera.updateProjectionMatrix();
  const renderer = new SoftwareRenderer({ interactiveTriangleBudget: 1 }); renderer.setSize(400, 400);
  return { scene, camera, renderer, render() { renderer.render(scene, camera); return renderer.getStats(); } };
}
function covers(paths, x, y) {
  return paths.some(batch => batch.polygons.some(poly => {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const a = poly[i], b = poly[j];
      if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
  }));
}
let checked = 0;
function check(name, run) { run(); checked++; }
check('full indexed mesh, cache reuse and camera invalidation', () => {
  const object = mesh([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0], [0, 1, 2, 0, 2, 3]);
  const f = fixture(object); assert.equal(f.render().drawnTriangles, 2); assert(covers(f.renderer.context.paths, 201, 200));
  const fills = f.renderer.context.fills; assert.equal(f.render().frameReused, true); assert.equal(f.renderer.context.fills, fills);
  f.camera.position.x = 1; f.camera.lookAt(0, 0, 0); assert.equal(f.render().frameReused, false);
  object.position.x = 200; assert.equal(f.render().drawnTriangles, 0);
  object.position.x = 0; assert.equal(f.render().drawnTriangles, 2);
});
check('real aperture remains open, shells remain complete and idle restores all parts', () => {
  const vertices = [-2, -2, 0, 2, -2, 0, 2, 2, 0, -2, 2, 0, -.6, -.6, 0, .6, -.6, 0, .6, .6, 0, -.6, .6, 0];
  const indices = [0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7];
  const lid = mesh(vertices, indices); lid.name = 'lid'; const f = fixture(lid);
  assert.equal(f.render().drawnTriangles, 8); assert.equal(covers(f.renderer.context.paths, 200, 200), false); assert(covers(f.renderer.context.paths, 90, 200));
  f.renderer.setInteractionState(true); assert.equal(f.render().drawnTriangles, 8); assert.equal(covers(f.renderer.context.paths, 200, 200), false);
  f.renderer.setInteractionState(false); assert.equal(f.render().quality, 'full'); assert.equal(f.renderer.getStats().drawnTriangles, 8);
  // Each independent component has at least one real triangle during preview.
  const big = new THREE.Mesh(new THREE.TorusGeometry(1, .2, 12, 80), material()); big.position.z = .2;
  const other = big.clone(); other.geometry = big.geometry; other.position.x = .7; f.scene.add(big, other);
  const full = f.render().sampledTriangles; f.renderer.setInteractionState(true); const preview = f.render();
  assert(preview.sampledTriangles < full); assert.equal(preview.drawnMeshes, 3);
  f.renderer.setInteractionState(false); assert.equal(f.render().sampledTriangles, full);
});
check('homogeneous near/frustum clipping preserves crossing triangles', () => {
  const f = fixture(mesh([-1, -1, 0, 1, -1, 0, 0, 1, 5.2]));
  assert(f.render().drawnTriangles > 0);
  for (const batch of f.renderer.context.paths) for (const poly of batch.polygons) for (const point of poly) for (const value of point) assert(Number.isFinite(value) && value >= -.001 && value <= 400.001);
  const outside = mesh([200, 0, 0, 201, 0, 0, 200, 1, 0]); f.scene.add(outside); assert.equal(f.render().culledMeshes, 1);
});
check('opaque side culling respects mirrored transforms', () => {
  const front = mesh([-1, -1, 0, 1, -1, 0, 0, 1, 0], null, material({ side: THREE.FrontSide }));
  const f = fixture(front); assert.equal(f.render().drawnTriangles, 1);
  front.scale.x = -1; assert.equal(f.render().drawnTriangles, 1);
  front.material.side = THREE.BackSide; assert.equal(f.render().drawnTriangles, 0);
  front.material.side = THREE.DoubleSide; assert.equal(f.render().drawnTriangles, 1);
});
check('continuous section clipping and changed plane invalidate cache', () => {
  const plane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0), mat = material({ clippingPlanes: [plane] });
  const f = fixture(mesh([-1, -1, 0, 1, -1, 0, 0, 1, 0], null, mat)); assert(f.render().drawnTriangles > 0);
  for (const batch of f.renderer.context.paths) for (const poly of batch.polygons) for (const [x] of poly) assert(x >= 199.999);
  assert.equal(covers(f.renderer.context.paths, 180, 230), false); assert.equal(covers(f.renderer.context.paths, 220, 230), true);
  plane.constant = -2; assert.equal(f.render().drawnTriangles, 0);
});
check('transparent overlapping triangles retain individual alpha compositing', () => {
  const object = mesh([-1, -1, 0, 1, -1, 0, 0, 1, 0, -1, -1, .1, 1, -1, .1, 0, 1, .1], null, material({ transparent: true, opacity: .22 }));
  const f = fixture(object); const stats = f.render(); assert.equal(stats.drawnTriangles, 2); assert.equal(stats.canvasFills, 2);
  for (const batch of f.renderer.context.paths) assert(Math.abs(batch.alpha - .22) < 1e-6);
  object.material.opacity = .4; assert.equal(f.render().frameReused, false); assert(Math.abs(f.renderer.context.paths[0].alpha - .4) < 1e-6);
});
check('material groups and mutable geometry are not cached as stale', () => {
  const object = mesh([-1, -1, 0, 0, -1, 0, -.5, 1, 0, 0, -1, 0, 1, -1, 0, .5, 1, 0]);
  object.material = [material({ color: '#ff0000' }), material({ color: '#00ff00' })]; object.geometry.addGroup(0, 3, 0); object.geometry.addGroup(3, 3, 1);
  const f = fixture(object); assert.equal(f.render().drawnTriangles, 2); assert.equal(new Set(f.renderer.context.paths.map(b => b.color)).size, 2);
  const p = object.geometry.attributes.position; for (let i = 0; i < p.count; i++) p.setX(i, p.getX(i) + 200); p.needsUpdate = true;
  assert.equal(f.render().drawnTriangles, 0);
});

// Small GLB reader for reproducible CPU benchmarks. It builds the same primitive
// Mesh nodes as GLTFLoader, preserves every index and node/material transform,
// and never accesses a browser or changes the original model.
async function loadGLB(filename) {
  const bytes = await readFile(filename); assert.equal(bytes.toString('ascii', 0, 4), 'glTF');
  const jsonSize = bytes.readUInt32LE(12), doc = JSON.parse(bytes.toString('utf8', 20, 20 + jsonSize)), binary = bytes.subarray(28 + jsonSize);
  const components = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }, types = { 5121: Uint8Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
  const accessors = new Map();
  function attribute(id) {
    if (accessors.has(id)) return accessors.get(id);
    const a = doc.accessors[id], v = doc.bufferViews[a.bufferView], T = types[a.componentType], width = components[a.type], start = (v.byteOffset || 0) + (a.byteOffset || 0), stride = v.byteStride || width * T.BYTES_PER_ELEMENT;
    let array;
    if (stride === width * T.BYTES_PER_ELEMENT) array = new T(binary.buffer, binary.byteOffset + start, a.count * width);
    else { array = new T(a.count * width); for (let i = 0; i < a.count; i++) array.set(new T(binary.buffer, binary.byteOffset + start + i * stride, width), i * width); }
    const result = new THREE.BufferAttribute(array, width); accessors.set(id, result); return result;
  }
  const materials = doc.materials.map(m => {
    const c = m.pbrMetallicRoughness?.baseColorFactor || [1, 1, 1, 1];
    return new THREE.MeshBasicMaterial({ color: new THREE.Color().setRGB(c[0], c[1], c[2]), side: THREE.DoubleSide, transparent: c[3] < 1, opacity: c[3] });
  });
  const primitiveGeometries = new Map();
  function geometry(p) {
    const key = `${p.attributes.POSITION}/${p.indices}`; if (primitiveGeometries.has(key)) return primitiveGeometries.get(key);
    const g = new THREE.BufferGeometry().setAttribute('position', attribute(p.attributes.POSITION)); if (p.indices !== undefined) g.setIndex(attribute(p.indices)); primitiveGeometries.set(key, g); return g;
  }
  let triangles = 0, meshes = 0;
  function node(id) {
    const n = doc.nodes[id], object = new THREE.Group(); object.name = n.name || '';
    if (n.matrix) { object.matrix.fromArray(n.matrix); object.matrixAutoUpdate = false; }
    else { if (n.translation) object.position.fromArray(n.translation); if (n.rotation) object.quaternion.fromArray(n.rotation); if (n.scale) object.scale.fromArray(n.scale); }
    if (n.mesh !== undefined) for (const p of doc.meshes[n.mesh].primitives) {
      assert(p.mode === undefined || p.mode === 4); const g = geometry(p); const part = new THREE.Mesh(g, materials[p.material] || material()); object.add(part); triangles += (g.index?.count || g.attributes.position.count) / 3; meshes++;
    }
    for (const child of n.children || []) object.add(node(child)); return object;
  }
  const group = new THREE.Group(); for (const id of doc.scenes[doc.scene || 0].nodes) group.add(node(id));
  const model = new THREE.Group(); model.rotation.x = Math.PI / 2; model.scale.setScalar(1000); model.add(group);
  const scene = new THREE.Scene(); scene.add(model);
  const bounds = new THREE.Box3().setFromObject(model), center = bounds.getCenter(new THREE.Vector3()), size = bounds.getSize(new THREE.Vector3());
  const camera = new THREE.PerspectiveCamera(38, 1.5, .01, Math.max(1000, size.length() * 50)); camera.up.set(0, 0, 1);
  camera.position.copy(center).add(new THREE.Vector3(1.1, -1.5, 1.25).normalize().multiplyScalar(Math.max(size.x, size.y, size.z) * 2.05)); camera.lookAt(center); camera.updateProjectionMatrix();
  return { scene, camera, center, model, meshes, triangles, bytes: bytes.length, jsonBytes: jsonSize };
}
// Baseline is the previous renderer's allocation/sort/draw workload. Camera is
// fully outside the assembly; there are no section planes for this comparison.
function baseline(scene, camera) {
  const start = performance.now(); scene.updateMatrixWorld(true); camera.updateMatrixWorld(true);
  const vp = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse), light = new THREE.Vector3(.3, -.5, 1).normalize(), faces = [];
  scene.traverse(mesh => {
    if (!mesh.isMesh || !mesh.visible || !mesh.geometry?.attributes.position) return;
    const pos = mesh.geometry.attributes.position, ix = mesh.geometry.index, mat = mesh.material, transformed = [];
    for (let i = 0; i < pos.count; i++) transformed.push(new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld));
    for (let i = 0, count = ix ? ix.count : pos.count; i < count; i += 3) {
      const points = [0, 1, 2].map(j => transformed[ix ? ix.getX(i + j) : i + j]);
      const normal = new THREE.Vector3().subVectors(points[1], points[0]).cross(new THREE.Vector3().subVectors(points[2], points[0])).normalize();
      const color = mat.color.clone().multiplyScalar(.58 + .42 * Math.abs(normal.dot(light))).getStyle(THREE.SRGBColorSpace), projected = points.map(p => p.clone().applyMatrix4(vp));
      if (projected.every(p => p.z < -1 || p.z > 1)) continue;
      faces.push({ points: projected, depth: projected.reduce((s, p) => s + p.z, 0) / 3, color });
    }
  });
  faces.sort((a, b) => b.depth - a.depth); const ctx = new Context();
  for (const face of faces) { ctx.beginPath(); face.points.forEach((p, i) => { const x = (p.x + 1) * 600, y = (1 - p.y) * 400; if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.closePath(); ctx.fillStyle = face.color; ctx.fill(); }
  return { frameMs: performance.now() - start, drawnTriangles: faces.length, canvasFills: ctx.fills };
}
check('raster aperture, alpha compositing, depth and overlay markers', () => {
  const f = fixture(mesh([-2, -2, 0, 2, -2, 0, 2, 2, 0, -2, 2, 0], [0, 1, 2, 0, 2, 3], material({ color: '#ff0000' })));
  const ctx = f.renderer.context;
  ctx.createImageData = (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) });
  ctx.putImageData = image => { ctx.image = image; };
  f.renderer.setBackend('raster');
  const sample = (x, y) => Array.from(ctx.image.data.subarray((y * 400 + x) * 4, (y * 400 + x) * 4 + 4));
  assert.equal(f.render().imageDataSubmissions, 1); assert.equal(f.renderer.getStats().canvasFills, 0); assert.equal(sample(200, 200)[3], 255);
  assert(sample(200, 200)[0] > 150 && sample(200, 200)[1] === 0);
  const overlay = mesh([-1, -1, 1, 1, -1, 1, 0, 1, 1], null, material({ color: '#0000ff', transparent: true, opacity: .5, depthWrite: false }));
  f.scene.add(overlay); f.render(); const blended = sample(200, 200); assert(blended[0] > 70 && blended[2] > 70); assert.equal(blended[3], 255);
  const behind = mesh([-1, -1, -1, 1, -1, -1, 0, 1, -1], null, material({ color: '#00ff00', depthTest: false })); behind.renderOrder = 101;
  f.scene.add(behind); f.render(); assert(sample(200, 200)[1] > 150); assert.equal(sample(200, 200)[0], 0);
  behind.material.depthTest = true; f.render(); assert.equal(sample(200, 200)[1], 0);
  // A real ring, with no center triangles: the backing image stays transparent.
  f.scene.clear();
  const ring = mesh([-2,-2,0, 2,-2,0, 2,2,0, -2,2,0, -.6,-.6,0, .6,-.6,0, .6,.6,0, -.6,.6,0], [0,1,5,0,5,4,1,2,6,1,6,5,2,3,7,2,7,6,3,0,4,3,4,7]);
  f.scene.add(ring); f.render(); assert.equal(sample(200, 200)[3], 0); assert.equal(sample(90, 200)[3], 255);
  ring.material.clippingPlanes = [new THREE.Plane(new THREE.Vector3(1,0,0), 0)]; f.render(); assert.equal(sample(90, 200)[3], 0); assert.equal(sample(310, 200)[3], 255);
  // Top-left coverage rule: a translucent square's two triangles have no diagonal double-alpha seam.
  f.scene.clear(); const square = mesh([-2,-2,0,2,-2,0,2,2,0,-2,2,0], [0,1,2,0,2,3], material({ transparent: true, opacity: .5 })); f.scene.add(square); f.render();
  assert.equal(sample(200, 200)[3], 128); assert.equal(sample(201, 200)[3], 128);
});
check('adaptive raster preview preserves CSS size and restores exact full pixels', () => {
  const object = new THREE.Mesh(new THREE.TorusGeometry(1, .2, 12, 80), material());
  const f = fixture(object), ctx = f.renderer.context;
  ctx.createImageData = (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) });
  ctx.putImageData = image => { ctx.image = image; };
  f.renderer.setBackend('raster'); f.renderer.interactiveTriangleBudget = 200;
  const full = f.render(), original = ctx.image.data.slice();
  assert.equal(ctx.image.width, 400); assert.equal(full.resolutionScale, 1);
  f.renderer.setInteractionState(true); const preview = f.render();
  assert.equal(f.renderer.domElement.style.width, '400px'); assert.equal(f.renderer.domElement.style.height, '400px');
  assert.equal(ctx.image.width, 300); assert.equal(preview.resolutionScale, .75);
  assert(preview.sampledTriangles < full.sampledTriangles); assert.equal(preview.drawnMeshes, full.drawnMeshes);
  // Very slow frames lower only the following preview's bounded budget and
  // resolution. Returning the pointer to rest must clear those limits.
  f.renderer._adaptPreview(200); f.renderer.render(f.scene, f.camera);
  assert(f.renderer.getStats().resolutionScale >= .5); assert(f.renderer.getStats().triangleBudget >= 1);
  assert.equal(f.renderer.getStats().frameReused, false); assert(ctx.image.data.some(x => x));
  f.renderer.setInteractionState(false); const restored = f.render();
  assert.equal(restored.quality, 'full'); assert.equal(restored.sampledTriangles, full.sampledTriangles);
  assert.equal(restored.resolutionScale, 1); assert.equal(ctx.image.width, 400);
  assert.deepEqual(ctx.image.data, original);
  assert.equal(f.render().frameReused, true);
});
const benchmarkIndex = process.argv.indexOf('--benchmark');
let benchmark = null;
if (benchmarkIndex >= 0) {
  capture = false; const real = await loadGLB(path.resolve(process.argv[benchmarkIndex + 1]));
  const renderer = new SoftwareRenderer(); renderer.setSize(1200, 800);
  const before = baseline(real.scene, real.camera);
  renderer.render(real.scene, real.camera); const cold = renderer.getStats();
  renderer.invalidate(); renderer.render(real.scene, real.camera); const warm = renderer.getStats();
  renderer.render(real.scene, real.camera); const unchanged = renderer.getStats();
  real.camera.position.x += 5; real.camera.lookAt(real.center); renderer.render(real.scene, real.camera); const moved = renderer.getStats();
  renderer.setInteractionState(true); renderer.render(real.scene, real.camera); const interactive = renderer.getStats();
  renderer.setInteractionState(false); renderer.render(real.scene, real.camera); const restored = renderer.getStats();
  assert.equal(cold.totalTriangles, real.triangles); assert.equal(warm.sampledTriangles, real.triangles);
  assert.equal(restored.sampledTriangles, real.triangles); assert.equal(restored.quality, 'full');
  assert.equal(unchanged.frameReused, true); assert.equal(interactive.drawnMeshes, restored.drawnMeshes);
  renderer.context.createImageData = (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) });
  renderer.context.putImageData = image => { renderer.context.image = image; };
  renderer.setBackend('raster'); renderer.render(real.scene, real.camera); const rasterCold = renderer.getStats();
  real.camera.position.x += 5; real.camera.lookAt(real.center); renderer.render(real.scene, real.camera); const rasterMoved = renderer.getStats();
  renderer.setInteractionState(true); renderer.render(real.scene, real.camera); const rasterInteractive = renderer.getStats();
  const rasterMovingFrames = [];
  for (let i = 0; i < 8; i++) {
    real.camera.position.x += 1.5; real.camera.position.y -= .5; real.camera.lookAt(real.center);
    renderer.render(real.scene, real.camera); const frame = renderer.getStats(); rasterMovingFrames.push(frame);
    assert.equal(frame.drawnMeshes, restored.drawnMeshes); assert.equal(frame.quality, 'interactive');
  }
  renderer.setInteractionState(false); renderer.render(real.scene, real.camera); const rasterRestored = renderer.getStats();
  assert.equal(rasterRestored.sampledTriangles, real.triangles); assert.equal(rasterRestored.drawnMeshes, restored.drawnMeshes); assert.equal(rasterRestored.imageDataSubmissions, 1);
  benchmark = { rasterCold, rasterMoved, rasterInteractive, rasterMovingFrames, rasterRestored, filename: path.resolve(process.argv[benchmarkIndex + 1]), ...Object.fromEntries(['meshes', 'triangles', 'bytes', 'jsonBytes'].map(k => [k, real[k]])), baseline: before, cold, warm, unchanged, moved, interactive, restored, warmSpeedup: before.frameMs / warm.frameMs, note: 'Node CPU/projection/sort/Canvas call workload; no native Canvas raster time or browser GPU/FPS measurement.' };
}
process.stdout.write(JSON.stringify({ tests: checked, passed: true, benchmark }) + '\n');
