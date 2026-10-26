# Software viewer performance

The CPU fallback renders the actual GLB triangles. The validated default is a typed-array raster backend; a Canvas path backend remains selectable. Its preview is a visualization,
not a collision or STEP check. The WebGL renderer remains the preferred browser
path. This change does not modify CAD, STL, the source assembly, print files,
requirements or authorization gates.

## Integration API

`SoftwareRenderer` keeps `render(scene, camera)`, `setSize`, `setPixelRatio`,
`setClearColor` and `domElement`. The constructor accepts `backend: "auto" | "raster" | "canvas"` (default auto; selects raster when ImageData is available). `setBackend()` changes it explicitly. New methods:

- `setInteractionState(true|false)`: while moving, use an explicitly identified
  interactive preview. The default budget is 30,000 source triangles; it is a
  target, because all small meshes and all base/lid triangles remain complete.
  Every visible component participates. On `false` the next frame processes all
  source triangles without sampling. Do not describe an interactive preview as
  a complete static review.
- `getStats()`: a copied record containing `quality`, `totalTriangles`,
  `sampledTriangles`, `visibleTriangles`, `drawnTriangles`, `visibleMeshes`,
  `drawnMeshes`, `resolutionScale`, `triangleBudget`, `cacheHits`, `frameReused`, `culledMeshes`, `backfaceCulled`,
  `zeroProjectedArea`, `canvasFills`, `backend`, `rasterPixels`, `imageDataSubmissions` and `frameMs`.
- `invalidate()`: request a fresh draw after a UI mode change. Matrix, position
  attribute version, index version, visibility, material color/opacity/side,
  draw range and clipping-plane changes are detected during rendering too.
- `dispose()`: release geometry, mesh and style caches when changing jobs. The
  caller still disposes Three.js geometries/materials as before.

The main app owns OrbitControls start/end scheduling and the preview/full-detail
status text. On interaction end call `setInteractionState(false)`, invalidate and
schedule a full draw. Schedule only when dirty. No render-loop code was changed
by this worker.

## Implemented work

Geometry indices and local coordinates are cached once per geometry. World
vertices, per-triangle flat-light shading and bounds are cached per mesh and
invalidated by real geometry/matrix changes. Camera changes project each shared
vertex once into typed arrays. There are no Vector3, Color, face objects or
point arrays allocated in the ordinary per-triangle path. Colors are shared
64-step shade palettes, with an intensity step of 0.42 / 63. Sorting operates on
typed triangle indices rather than objects.

Meshes entirely outside one homogeneous view-frustum plane are rejected using
their conservative world bounds. Bounds never replace visible mesh geometry.
Boundary triangles use continuous polygon clipping in world space and then
homogeneous near/far/side-plane clipping. Triangles with exactly zero projected
area have no drawable pixels and are counted separately. Single-sided materials
respect their side and mirrored transforms; DoubleSide remains double-sided.

Opaque consecutive triangles of identical color batch into a single Canvas
path without reordering. Transparent triangles remain separate fills so their
alpha blending does not change through batching. Material groups and shared
indexed geometry are supported. An unchanged view reuses the completed canvas.

## Actual checks

`/home/wujie/.cache/justbuild/venv/bin/python -m pytest -q tests/integration/test_viewer_performance.py`

Result: **1 pytest passed**, running **9 JavaScript geometry/behavior checks**:
indexed geometry and reuse, an actual aperture remaining open, every component
participating during interaction with complete idle restoration, near/frustum
clipping, side/mirror behavior, continuous moving sections, transparent overlap,
multi-material groups, in-place geometry edits, and exact full pixel restoration after adaptive preview. Raster checks also assert transparent hole pixels, depth rejection, marker overlay, transparent color mixing and no shared-diagonal double alpha. The test uses Three.js and
records the renderer's output polygons; it does not operate a browser or launch
any GUI or printer.

Reproducible real-assembly benchmark:

```sh
node --max-old-space-size=4096 tests/integration/test_viewer_performance.mjs --benchmark data/v821-redesign/review-optimization/lossless-01/review-light.glb
```

The reader preserves all glTF primitive indices, source node transforms and
colors. Both inputs contain **384,357 source triangles**. The optimized GLB has
545 drawable meshes and 10,713,216 bytes; the original has 64,840 primitive meshes
and 43,367,816 bytes. The optimized GLB was independently produced by the review
mesh worker with no triangle/normal/coordinate removal.

Measured once on this workstation, Node v22.23.2, 1200 × 800 review canvas:

| Workload | Old GLB | Lossless merged GLB |
| --- | ---: | ---: |
| Previous renderer allocation/sort/Canvas-call workload | 448.8 ms | 450.8 ms |
| New full cold frame | 535.5 ms | 188.5 ms |
| New full cached-geometry frame | 200.8 ms | 104.4 ms |
| Unchanged completed canvas reused | 72.0 ms | 1.5 ms |
| Full frame after camera movement | 192.0 ms | 94.4 ms |
| Interactive preview | 158.3 ms | 18.2 ms |
| Full frame restored on stop | 181.6 ms | 78.8 ms |

For the merged GLB, all **545 / 545 components** participated in full and
interactive modes. Full processing visits all **384,357** source triangles;
**383,436** produce nonzero projected area in this camera view (921 exactly
edge-on/zero-area projections). Interaction samples 82,915 source triangles,
including complete upper/lower covers, then restores all 384,357 on stop.

These are CPU, projection, clipping, sorting and Canvas API call costs measured
with a recording/no-op Canvas sink. They **do not include native browser Canvas
rasterization, GPU execution, network fetch, GLTFLoader import or browser FPS**.
The optional Canvas path still issues about 335,697 native fill calls on this input; the raster backend eliminates these calls. The
merged model and main app's dirty-frame scheduler are essential: replacing only
the CPU renderer while retaining 64,840 tiny primitive meshes has a slower cold
frame. Do not present these numbers as observed browser responsiveness.

## Remaining limits

Both fallbacks use flat lighting. The raster backend has an interpolated Float64 pixel depth buffer; the optional Canvas path uses painter ordering. Intersecting translucent triangles and cyclic transparency can still have ordering artifacts. It does not
render textures, skins, instancing, glTF animation, section caps or multiple clipIntersection unions. The project's review
model is static untextured solid triangles using ordinary intersection clipping.
WebGL is preferred where available. Review screenshots and perceived browser performance still need a fresh in-browser check by the main owner.

Owned files: `src/justbuild/web/software-renderer.js`, `src/justbuild/web/software-rasterizer.js`,
`tests/integration/test_viewer_performance.mjs`,
`tests/integration/test_viewer_performance.py`, this document. No dependencies
were added, no public links opened, and no native program or printer started.


## Validated typed-array raster backend

The default `auto` mode chooses raster when the Canvas exposes `createImageData`
and `putImageData`, otherwise keeps the path backend. It renders all clipped
triangles into a device-resolution RGBA buffer and a Float64 depth buffer, then
submits **one** ImageData update. `setBackend('canvas')` remains an explicit
fallback for diagnosis or a browser where the raster appearance is unsuitable.

Rendering respects material `side`, `opacity`, `transparent`, `depthTest`,
`depthWrite` and mesh `renderOrder`. Each renderOrder layer draws opaque geometry
front to back, then transparent geometry back to front. A high-order marker with
`depthTest:false` overlays the model without altering its geometry. The pixel
coverage top-left rule assigns shared triangle edges once, avoiding double-alpha
seams on translucent coplanar faces. Source triangles are not replaced by pixels
before clipping or visibility processing. Every source triangle is considered
at idle; subpixel or occluded triangles naturally need not produce a visible
pixel. The raster has no multisample antialiasing; small edge details can look
jagged. WebGL remains preferable when supported.

A second run with actual typed-array raster work on the **same 545-mesh,
384,357-triangle** input measured:

| Workload | Measured CPU time | Canvas fills | ImageData submissions |
| --- | ---: | ---: | ---: |
| Previous renderer (Canvas no-op call sink) | 467.9 ms | 384,357 | 0 |
| New Canvas path after movement | 106.5 ms | 334,579 | 0 |
| Raster first full frame | 147.4 ms | 0 | 1 |
| Raster after camera movement | 125.8 ms | 0 | 1 |
| Raster interaction preview | 51.4 ms | 0 | 1 |
| Raster full restoration | 126.7 ms | 0 | 1 |

The raster movement frame performs 526,178 real pixel writes and the interactive
frame 537,330; all 545 components remain in both views, and the final full frame
visits all 384,357 source triangles. Raster CPU cost includes the real
per-triangle scan conversion, depth checks and color composition. The Node sink
still does not include the browser's ImageData transfer/display cost or the
native Canvas fill cost from the other columns. Therefore these times are not
browser FPS and do not directly measure the user-visible speedup. Main-owned
browser checks are required to measure that result. No GUI was controlled by
this benchmark.


## Adaptive movement preview (2026-10-24)

`SoftwareRenderer` now also accepts `interactiveResolutionScale` (default 0.75,
clamped to 0.5–0.85) and `targetFrameMs` (default 28, clamped to 16–50).
Interaction starts with a 30,000 triangle target and 75% internal pixel width /
height. CSS width and height are unchanged, so the main page's labels and
measurement overlay remain aligned to the same viewport. Slow interactive
frames reduce the next frame's budget, to a minimum target of 12,000 (or the
caller's smaller configured budget), and reduce pixel scale by 0.05 down to 0.5.
Fast frames recover toward the configured budget and scale. The target remains
soft because complete base/lid and small-component geometry always takes
priority. The Canvas path retains full pixel dimensions. On interaction end,
the next draw immediately restores every source triangle and the full configured
pixel ratio. Nothing is saved back into the GLB, CAD or print artifact.

The raster depth buffer also makes per-triangle depth sorting unnecessary for
ordinary opaque layers. Those layers keep their source order; transparent
triangles still sort back to front, and unusual opaque layers with disabled
depth tests or writes retain their prior depth sorting. This preserves the
explicit marker layer and alpha behavior while reducing full-frame work.

A fresh Node raster run of the same 10.7 MB / 545-mesh V821 model measured:

| Workload | CPU frame time | Source triangles processed | Internal pixel scale |
| --- | ---: | ---: | ---: |
| Before this refinement: full moved frame | 142.0 ms | 384,357 | 1.0 |
| Before this refinement: movement preview | 63.4 ms | 82,915 | 1.0 |
| Updated full moved frame | 112.3 ms | 384,357 | 1.0 |
| Updated first movement preview | 28.6 ms | 29,702 | 0.75 |
| Updated continuous movement, 8 frames | 18.2–20.2 ms | 29,702 each | 0.75 |
| Complete restoration after movement | 94.5 ms | 384,357 | 1.0 |

Every one of the 545 components participated in each measured movement frame
and in the complete restored frame. The new contract test checks unchanged CSS
size, bounded adaptive reduction, an actual nonempty reduced raster, complete
triangle restoration, byte-for-byte equality of the restored full RGBA pixels,
and idle frame reuse. These remain Node CPU measurements, including real
pixel rasterization but excluding the browser's ImageData display cost and
input/event scheduling. They are not claimed browser FPS. A cached unchanged
view reuses its existing canvas; the main page owns event-driven scheduling.
