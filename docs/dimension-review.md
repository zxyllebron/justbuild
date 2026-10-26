# In-image dimensions

Updated 2026-10-24. This module draws SVG measurement annotations over the existing viewer. It does not add any mesh, triangle, renderer pass, model transform or collision claim.

## Files and integration

- `src/justbuild/web/dimension-review.js`: dimensions from explicit CAD metadata and confirmed constraints, mode selection, screen projection and label placement.
- `src/justbuild/web/dimension-review.css`: scoped styles for the SVG, labels and source caption.
- `tests/integration/test_dimension_review.mjs` and `.py`: ten Node behavioral contracts, with a pytest driver. No browser/network/native app/printer calls.

The main chat owns `app.js`, `index.html`, `style.css` and the rest of integration. Include the stylesheet, provide an empty overlay container inside the viewport, and use:

```js
import { DimensionReview } from './dimension-review.js';
const dimensionReview = new DimensionReview(camera, viewport, document.getElementById('dimensionoverlay'));
dimensionReview.update(job);
dimensionReview.setVisible(true); // Set when the user enables 测量.
dimensionReview.setMode('shell'); // shell | board | holes | feature
// The existing functional component callback can supply a functional feature or a matching port.
dimensionReview.setFeature(feature); // Selects feature mode; preserves visibility.
// Call after renderer/control camera updates in the existing render loop.
dimensionReview.project();
```

`setFeature(null)` returns feature mode to shell mode. `update(job, {bounds})` accepts integration view bounds but never uses them as a source of numeric dimensions. `getSummary()` returns units, shell/board bounds and sizes, valid original hole count/diameters, original hole-center X/Y spans, source STEP/request SHA-256 and the currently displayed measurements. `deriveDimensionData(job)` and `layoutDimensionLabels(...)` are exported for direct behavioral checks.

The overlay uses `pointer-events:none`, so orbit/zoom/pan keep working. The default layer is z-index 6. Main controls such as model status and the expanded-view exit should be above it. The top 42 px and bottom source caption are reserved during label placement. Six hole-mode labels fit without overlap in the tested 319 × 420 px viewport. On an exceptionally tiny view with no physically available label area, labels remain clamped but overlap cannot be universally avoided.

## Numeric sources

All fields must be finite and CAD metadata must explicitly declare `units: mm`.

| Display | Source | Meaning |
| --- | --- | --- |
| Shell length, width, height | Union of `metadata.parts[id=base/lid].bounds` | Axis-aligned BRep bounds of both assembled shell parts. Requires both parts. No fallback to screenshot, mesh, or configured outer box. |
| Board length, width, thickness | `metadata.input_solids[role=pcb].bounds`, falling back to `metadata.board_placement.pcb_bounds` | Board BRep bounds recorded during STEP extraction, excluding component height. |
| P hole diameter | `metadata.mounting_holes`, or confirmed request mounting-hole entries when CAD field is absent | The supplied PCB hole diameter, not the shell pilot-hole or lid screw diameter. |
| Hole-center X/Y spans | Min/max of those original hole centers in each board axis | Axis-aligned center-pattern span. An arbitrary pattern is not claimed to be rectangular and all adjacent pair pitches are not claimed equal. |
| Interface opening width/height/diameter | Explicit matching `metadata.ports` or confirmed request ports | Generated design-window dimensions, annotated at the relevant shell face; no inference from component dimensions. |

Invalid/missing units, missing BRep bounds, missing dimensions or unmatched/internal features are not filled with defaults. A feature without explicit external opening dimensions reports that absence. Source captions distinguish CAD entity extents, confirmed PCB holes and interface design openings. Detailed source strings are retained on measurements; STEP and request hashes are available in `getSummary()` and the source caption tooltip.

Numbers display two decimal places and include mm. This is a formatting choice, not a manufacturing tolerance or an accuracy claim. Exploded display does not change numerical dimensions or assembly-coordinate anchors. This avoids treating the visualization's arbitrary separation distance as a physical design dimension.

## Real V821 source readback

Read from existing real-job CAD result metadata for `019d8f0d6d92426aa96618210deb469a`; no re-generation or new geometry verification was run for this display feature:

- Source STEP SHA-256: `2b096e5a608d7089dbc6062cfb4fd410af0d810e17436928e0bbb47b45dd51cc`.
- Assembled shell BRep bounds: min `[-12.42677714379, -10.9872999746, -7]`, max `[110.987500635, 74.276520264566, 15.086160363453]` mm.
- Shell annotation: `长 123.41 mm`, `宽 85.26 mm`, `高 22.09 mm`.
- PCB annotation: `长 99.97 mm`, `宽 59.97 mm`, `厚 1.59 mm`.
- Four original mounting holes annotate approximately `Ø2.00 mm`; their board-axis center pattern spans round to `X 96.00 mm`, `Y 56.00 mm`.
- USB-left window annotates explicit design width `12.94 mm`, height `8.20 mm`. These values do not establish physical plug, latch or button-travel acceptance.

## Checks actually run

- `node tests/integration/test_dimension_review.mjs`: passed ten behavioral checks.
- `/home/wujie/.cache/justbuild/venv/bin/python -m pytest -q tests/integration/test_dimension_review.py`: one passed in 0.10 s.
- Contracts cover BRep source precedence, source hash retention, missing/invalid metadata, missing lid, no mesh fallback, all three shell/board axes, thickness, four diameters and derived hole spans, rectangular/circular window dimensions, internal/unknown features, source-coordinate face placement, 319 px label containment and non-overlap, finite SVG coordinates, camera reprojection, behind-camera hiding, idle node reuse and no geometry/input mutation.

Actual browser interaction and visual appearance are integration checks for the main chat. Dimension annotations are review aids; collision, fit, assembly motion, thermal/RF performance and physical acceptance remain with their own checks.
