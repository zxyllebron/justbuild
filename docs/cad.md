# CAD owner 02: enclosure generator and integration handoff

Implemented and locally tested, 2026-10-24. Owned paths only: `src/justbuild/cad/`, `tests/cad/`, this document.

## Entry point and dependencies

`from justbuild.cad import generate`; `generate(request_path: Path, output_dir: Path) -> Path`.
The returned path is `<output_dir>/cad-<unique-id>/cad_result.json`. Every invocation writes a fresh run;
partial files from a failed run are diagnostics only and never appear as usable artifacts. No Git operations,
desktop CAD, printers, account operations or shared environment-file edits are part of this module.

Tested dependency target: Python 3.12, `cadquery==2.6.1` (OpenCascade via `cadquery-ocp`), pytest 8.
The CAD owner installed these only in `/tmp/justbuild-cad-venv`; this is an ephemeral test environment.
Main owner should pin a tested CAD kernel in the shared packaging/lockfile. CAD import is lazy: unavailable
dependencies return `unsupported`, while malformed/incomplete requests still produce JSON without a kernel.
Output-directory permission/I/O errors that prevent writing a result necessarily raise `OSError`.

```bash
cd /media/wujie/T9/justbuild
PYTHONPATH=src /tmp/justbuild-cad-venv/bin/python tests/cad/synthetic_fixture.py --output-dir /tmp/justbuild-cad-fixture
PYTHONPATH=src /tmp/justbuild-cad-venv/bin/python -m justbuild.cad /tmp/justbuild-cad-fixture/request.json --output-dir /tmp/justbuild-cad-artifacts
PYTHONPATH=src /tmp/justbuild-cad-venv/bin/python -m pytest tests/cad -q -p no:cacheprovider --basetemp=/tmp/justbuild-cad-pytest
```

CLI exit codes: 0 `ok`, 1 `failed`, 2 `needs_input`, 3 `unsupported`. Stdout ends with the result path.

## Deliberately constrained input dialect (proposed shared-interface additions)

See `tests/cad/example_request.json` for a complete **synthetic** request. It is not a measurement of,
or confirmation for, any physical board. Main owner must integrate these fields into request validation
and proposal confirmation; their presence here does not change the shared architecture/schema by itself.

- `board.metadata.pcb_solid_index`: caller-selected zero-based solid index in the complete STEP import;
  `pcb_selection_source`: provenance of that choice. No semantic PCB/connector recognition is claimed.
  STEP can contain multiple roots/solids but must not contain dangling surfaces or edges.
- `constraints.board_frame`: origin and orthonormal, right-handed axes **in STEP world millimeters**.
  All STEP roots are transferred with OCC's target length unit explicitly set to 1 mm. Hole, port, service and keepout coordinates
  are in this board frame. Exported STEP parts and assembly use the same board frame.
- By default (`board.metadata.pcb_geometry_mode: "rectangular_declared_holes"`), the selected PCB must equal
  a rectangular, flat, board-frame-aligned slab minus exactly its declared cylindrical through holes
  (checked with BRep symmetric difference). Opt-in `"original_outline"` preserves the actual original
  bottom-face outer wire and every inner wire, including nonmount holes and slots. It extrudes that exact
  section through the PCB thickness and proves equality with the entire original solid by BRep symmetric
  difference. A nonconstant-thickness PCB, multiple underside faces, or unsupported orientation remains
  rejected. Mounting-hole declarations in this mode need not misclassify every via or connector slot as a
  mounting hole: each declared bore must be empty in the original PCB, while its complete support annulus
  must be material in the original PCB through the full board thickness. This also rejects incorrect hole
  diameter/axis location, nearby cutouts removing support, and support footprints extending beyond an
  irregular contour. The default rectangular mode remains strict for existing callers.
- `constraints.feature_review`: `mounting_holes_complete`, `ports_complete`, `keepouts_complete` must each be
  true, with `source`. Empty ports/keepouts are allowed only after this explicit review. At least three
  non-collinear PCB mounting holes are required in this first structure.
- Each feature has a unique `id` and `source`. Holes use `reference: "pcb"`, `center_mm`, Z-parallel unit
  `axis`, `diameter_mm`. The center's Z lies within the slab. Complete post footprints must fit inside the
  PCB extent, and original-outline mode additionally proves the full support ring lies in actual PCB
  material; confirmed holes and posts may not overlap or be moved automatically.
- Ports use `access`: `external`, `maintenance`, `internal`, `covered`. Internal/covered entries create no
  opening. Active ports require `shape: "rectangle"` or `"circle"`, `face` in `x_min/x_max/y_min/y_max/z_min/z_max`,
  `center_mm` on the mating/service plane, `insertion_direction` **from exterior toward the case**,
  `width_mm`/`height_mm` or `diameter_mm`, and `service_envelope: {min_mm, max_mm, source}`. Rectangle width is
  along the lower-index tangential axis and height along the other (X face: Y/Z, Y face: X/Z, Z face: X/Y).
  The service box contains the mating point on both tangential axes, ends at that mating plane, extends past the case exterior, and must have zero BRep
  common volume with both the case and imported assembly. Mating face contact alone is allowed. Unknown
  plug/tool clearance returns `needs_input`; angled paths, non-box envelopes and shaped openings are unsupported.
- Keepouts: `shape: "box"`, `purpose: "no_material"`, board-frame `min_mm`, `max_mm`, `source`.
  These are subtracted from both parts. They cannot remove protected support/fastener/anchor material.
- `manufacturing` requires `material_id`, `source`, `wall_thickness_mm`, `base_thickness_mm`,
  `lid_thickness_mm`, `clearance_xy_mm`, `clearance_top_mm`, `clearance_bottom_mm`, `standoff_height_mm`,
  `geometry_tolerance_mm`, `boolean_volume_tolerance_mm3`, `mesh_linear_tolerance_mm`,
  `mesh_angular_tolerance_rad`. No actual production material or clearance is silently defaulted.
  Geometry tolerance is at most 0.01 mm; structural dimensions/clearances exceed ten tolerances;
  the explicitly supplied Boolean volume tolerance is at most `1000 * length_tolerance^3`.
- `manufacturing.review_mesh_linear_tolerance_mm` and `.review_mesh_angular_tolerance_rad` are optional
  display-only GLB tolerances. Each must be finite, positive and at most 1 mm / 1 rad respectively. When
  omitted they independently fall back to the corresponding existing `mesh_*` value. Their values never
  change CAD geometry, BRep checks or printable STL tessellation. Explicit review fields use a separate
  geometry copy without cached triangulations and OCC's absolute (`isRelative=False`) mesher; GLB writing
  then consumes that mesh without CadQuery's implicit relative remesh. When no review fields are supplied,
  the legacy CadQuery meshing path remains unchanged. `metadata.display_mesh` records the
  actual display values, printable STL values and `authoritative_geometry: false`. For the real model the
  caller can use STL 0.05 mm / 0.12 rad and display 0.25 mm / 0.4 rad; a coarser display is not new collision
  evidence and cannot change design acceptance.
- `manufacturing.pcb_mount` and `.lid_fastener`: `type: "self_tapping_blind_pilot"`, `screw_id`,
  `post_diameter_mm`, `pilot_diameter_mm`, `pilot_depth_mm`, `clearance_diameter_mm`. Lid fastening also
  needs `head_diameter_mm`, `head_depth_mm`. Pilot < screw clearance < post; radial ligament is at least
  wall thickness by default. `pcb_mount.minimum_radial_ligament_mm` can explicitly supply an independent
  support-post ligament without thinning the enclosure walls; it must be positive and exceed ten geometry
  tolerances, and the actual post-minus-clearance annulus must meet it. Lid-post requirements continue to
  use the enclosure wall thickness. This geometric parameter is not a strength qualification. PCB holes
  must clear the stated screws. Pilots remain blind, and counterbores leave
  lid material beneath the head and at least wall thickness at the outer corner edges. Screw length, torque,
  thread engagement and pullout strength are unverified.

## Construction and exported coordinate systems

The outer rectangle encloses the complete imported assembly XY envelope plus `clearance_xy_mm`, a full
lid-post diameter on each side, and the wall. This conservative reservation puts four automatic lid screw
columns outside the imported assembly. Their cylinders overlap corner walls by `min(wall/2, post_radius/4)`;
the exact centers and overlap are recorded. PCB support tops coincide with the selected PCB underside;
the floor lies `standoff_height_mm` below it. Imported underside components must retain the explicit bottom
clearance. The flat lid underside lies `clearance_top_mm` above the highest imported solid. No lip, gasket,
hinge, nut trap, heat-set insert or actual screw solid is generated. No repair or relocation is performed.

Successful results advertise hashed `base_step`, `lid_step`, `assembly_step`, `normalized_input_step`,
`base_stl`, `lid_stl`, `source_step`, `request_snapshot`, plus `review_glb` when its export succeeds.
STEP is authoritative. The unmodified `source_step` snapshot retains its internally declared source units;
all generated STEP geometry and dimensional metadata are in mm. STL is separate per part, translated to XY minimum zero and bed Z=0 with no rotation;
`metadata.stl_placements` records the transformation from the assembly frame. Slicers must use these meshes
as individually placed parts, not as an assembled pair. Mesh tolerances are recorded and do not define BRep validity.

GLB contains named base/lid/input-solid nodes and transparent case materials. CadQuery 2.6.1's exporter
rotates Z-up to Y-up but leaves mm-valued vertices; this module adds an explicit root scale of 0.001,
preserving the native binary chunk. The resulting glTF scene is in meters with mapping `[x,z,-y]*0.001`.
Consumers must honor node transforms; raw vertex buffers are not assembly-world coordinates.

Results record request and consumed STEP hashes, a private run-local input snapshot, selected-solid source,
solid inventory/bounds/volumes, generated parameters, assembly placement, exact feature requests, screw
locations, intentional PCB-support and base/lid contacts, kernel identity and tolerances.
Invalid requests return `needs_input`; unsupported primitives/PCB representations return `unsupported`;
invalid solids, collisions, protected-feature cuts or clearance failures return `failed`. `ok` only means
generation and the listed local checks completed. Independent verification remains mandatory.

## Evidence and limits

Final local check: **35 passed in 14.00s**, Python 3.12.3, pytest 8.4.2, CadQuery 2.6.1,
`cadquery-ocp` 7.8.1.1.post1. Only `tests/cad/` was executed; this is not a project-wide integration verdict.
The tests cover valid solid generation and re-import, actual pilot/annulus/counterbore material, opening and
keepout voids, six cardinal opening directions, service envelopes, forbidden support cuts, component/post
collisions, undeclared PCB holes, rigid rotated/translated board frames, multiple STEP roots, mixed free
surface rejection, a meter-encoded STEP converted to mm, STL bed placement, GLB units/materials, missing
mechanical requirements, malformed JSON, missing kernel and fresh-directory behavior on repeated runs.

Persistent synthetic evidence is under `tests/cad/evidence/synthetic-20261024/`:

- `input/synthetic_board.step`, `input/request.json`: synthetic 60 x 40 x 1.6 mm slab and cuboid connector.
- `output/cad-etrd36eb/cad_result.json`: `status: ok`, 17 local checks `pass`, 4 explicitly `not_checked`,
  9 hashed artifacts. Case outer dimensions **88 x 64 x 18.6 mm**. Every part is a valid positive-volume
  single solid; assembly STEP re-imports as four solids (base, lid, synthetic PCB, synthetic connector).
- `output/cad-etrd36eb/{base,lid,assembly,normalized_input}.step`, `{base,lid}.stl`, `review.glb`,
  original `source.step` and `request_snapshot.json`.
- `evidence.json`: exact test command, counts and result pointer; `preview.png`: a tessellated visual aid,
  not collision evidence. All dimensions and confirmations in this fixture are synthetic only.

Authored source/test files: `src/justbuild/cad/{__init__,__main__,request,kernel}.py`,
`tests/cad/{synthetic_fixture,test_generate}.py`, `tests/cad/example_request.json`, and this document.
No main-owned, verification-owned or printing-owned files were edited.

Implemented checks use BRep validity/count/volume, PCB symmetric difference, full requested opening/keepout
emptiness, service-box emptiness, final-pose case/input/base/lid common volumes, protected material and
STEP export/re-import validation. A browser model is not evidence for those checks.
Thermal suitability, screw/hardware qualification, continuous assembly paths, mesh watertightness, slicing,
print completion and physical fit remain `not_checked`. CAD generation must not authorize printing.

## Main-owner integration actions

1. Add this dialect to the confirmed-proposal schema/UI and preserve its provenance/acknowledgements.
2. Consume the returned nested result path; resolve/hash artifacts against its parent. CAD checks do not
   replace the verification module's independent verdict.
3. Verification should use `normalized_input_step` or apply `board_frame` to the original STEP; do not
   compare world-frame source solids directly with board-frame output parts. Map intentional contacts
   explicitly; do not globally waive all base/board contacts or collisions.
4. Honor STL placement and GLB root transforms. Bind review and downstream slicing to actual artifact hashes.
5. Pin the tested CAD dependency. No shared dependency/environment files were changed by CAD owner.

## Real V821 STEP investigation and minimum extension (2026-10-24)

The source supplied by the user is `/home/wujie/下载/3D_V821L2WBX_1_2026-10-24.step`,
62,527,894 bytes, SHA-256 `2b096e5a608d7089dbc6062cfb4fd410af0d810e17436928e0bbb47b45dd51cc`.
This is separate from all earlier synthetic example evidence. Owner-specific investigation scripts and
JSON evidence are under `data/v821-redesign/cad-analysis/`:

- `inventory.json`: full-root import with 543 solids, 64,691 faces and 544 shells. The complete assembly
  bounds are `[-1.426777143792, 0.012700025400, -1.413839836546]` to
  `[99.987500635001, 63.276520264566, 10.086160363453]` mm.
- `pcb-geometry.json`: source solid 0 is the valid PCB, bounds
  `[0.012700025400,0.012700025400,0]` to `[99.987500635001,59.987419354839,1.586156972314]` mm.
  Its 76 faces include split semicylindrical faces for the real bores and several smaller holes/slots.
  Recursive leaf inspection returns only `Solid: 543`, with zero non-solid leaves. Independent verification
  subsequently located the extra shell inside solid 541 (two shells); shell count alone does not establish
  a free surface. No geometry was dropped or repaired.
- `support-footprints.json`: the real PCB underside contains 31 inner wires. Extruding all original
  outer/inner wires through 1.586156972314 mm gives a valid solid with symmetric difference **0.0 mm³**
  against the imported PCB. The four true mounting bores have diameter **2.00000508001 mm**, not the old
  example's 3 mm. The exact XY axes are `(1.999654559309,57.999822199644)`,
  `(1.999679959360,2.000454660909)`, `(97.999829819660,2.000185420371)`, and
  `(98.000411480823,57.999723139446)` mm.

At those unchanged source axes, a **3.8 mm outer post** has a full bearing ring inside the original PCB.
Each ring has 8.199540866527 mm² contact area; all four exact bore/PCB intersections and all four
ring-minus-PCB missing-material volumes are **0.0 mm³**, evaluated at length tolerance 0.001 mm and Boolean
volume tolerance 1e-7 mm³. These are source-board/support geometry findings, not enclosure fit approval.

Recommended **explicit prototype** fastener geometry is M1.6 self-tapping, pilot 1.2 mm (or a separately
calibrated 1.3 mm), screw clearance 1.8 mm, post 3.8 mm and `minimum_radial_ligament_mm: 1.0`, while the
enclosure wall remains 2.0 mm. Material-specific pilot sizing, screw head/length, pullout, torque and physical
assembly remain unverified. No mounting axis is moved to fit a larger post.

The minimum kernel change is therefore opt-in original-section proof plus independent declared post
ligament. The source remains fully present for all case/component and service-envelope BRep checks.
This geometry investigation itself did not assert a reviewed connector list or mark `ports_complete`.
The main owner subsequently supplied the frozen real-board interface request used in the generation
continuation below. CAD generation does not establish independent verification, slicing, or physical fit.

`tests/cad/test_original_outline.py` adds 15 checks covering exact contour/slot/via preservation, wrong
bore diameter/axis rejection, support damage by other cutouts, nonconstant board thickness, default strict
mode, explicit small-post ligament, near-edge Ø2 holes with actual small-post generation and unchanged
2 mm case walls, invalid ligament values and unsupported geometry modes. Final combined command
`/home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/cad -q` returned **50 passed in 18.33 s**.

The subsequent display-mesh extension adds `tests/cad/test_review_mesh.py`. Its actual two-run generation
test confirms coarser GLB has fewer real triangle indices, both printable STLs retain the same actual
triangle counts, and both base/lid STEP solids have BRep symmetric difference below 1e-7 mm³. Ten input
boundary checks reject nonpositive, nonfinite, Boolean and excessive display tolerances. The targeted
command `.../python -m pytest tests/cad/test_review_mesh.py -q` returned **11 passed in 3.90 s**.

After the absolute display-meshing fix and run-local atomic `progress.json` instrumentation, the final
combined CAD suite `.../python -m pytest tests/cad -q` returned **61 passed in 22.78 s**. Progress reports
identify request validation, source import/validity, original PCB proof, required openings, protected
material checks, service volumes, component nonpenetration, STEP/STL/assembly/GLB exports and completion.
These are execution stages, not acceptance claims. The real-board invocation is tracked separately under
`data/v821-redesign/cad-analysis/live-generation-progress.json`.

## Real-board continuation, source fidelity and optional assembly packaging

The real job is `019d8f0d6d92426aa96618210deb469a`. The final frozen mechanical request SHA-256 is
`636451e11bedbd0cfdc3e6813a924db66b676775db34804ee93ecd09ceeb49f2`. The main owner mapped the real
components and supplied the opening/service constraints; the CAD owner did not invent their approval.

Two failed generation records remain immutable:

- `generating/cad-5hhqardd/cad_result.json`: full source validity, PCB and opening checks completed, then a
  null Common result interrupted the first service check. The revised main request also moved the SW4
  prototype approach window away from the adjacent CAM connector while retaining its dimensions and
  complete switch footprint; no source component or mounting axis moved.
- `generating/cad-r68xeju9/cad_result.json`: required base/lid STEP, STLs and complete GLB were produced,
  and source/PCB/opening/service/final-pose checks passed. Optional reencoding of the entire source to
  `normalized_input.step` then changed the calculated volume by **0.5261451013302576 mm³**, exceeding
  the unchanged **1e-7 mm³** tolerance. That reencoded PCB assembly is rejected, not relabelled passing.

Source collision checks now retain every original solid and use conservative material-bound screening
followed by exact per-original-solid BRep Common for candidates. Reported aggregate volumes are explicitly
the conservative **sum of per-solid common volumes**, not an exact union volume. No arbitrary null shape
is treated as empty; an uncompleted/null candidate operation remains an error. The full source inventory
and original geometry are still exported/preserved, including mutually touching/overlapping electronic
subparts. Source validity-derived bounds/volumes are reused within one invocation rather than recomputed
with a redundant full validity scan. `tests/cad/test_component_common.py` verifies preservation of genuine
overlapping source subparts and the reported measurement contract (**1 passed in 2.01 s**); the preceding
61-test CAD regression suite passed in 20.29 s.

For the frozen identity-frame, native-mm request, `src/justbuild/cad/recovery.py` implements the explicitly
scoped `resume_identity_source_preservation(request_path, previous_result_path, output_dir)` continuation.
It verifies request/source hashes, exact identity frame, OCC `FileUnits == ["millimetre"]`, all textual
length SI units as millimeters and absence of conversion-based units. It creates a **fresh** result, copies
the original source bytes to `source.step` and `normalized_input.step`, and proves exact SHA-256 equality.
No geometry translation, deletion, repair, replacement or reencoding occurs in that source path.

The required base/lid checkpoints are reimported, checked for BRep validity and single-solid count, and
compared to the generated record's mass/bounds at the original strict tolerances. Existing generated
STL/GLB checkpoints are retained and bound by fresh artifact hashes. Reused checks explicitly carry the
prior failed-result hash and individual check index, rather than pretending they were rerun. Independent
verification of the resulting required geometry remains mandatory.

Fresh result: `generating/cad-preserved-787nmihw/cad_result.json`, local generation status **ok**. It contains
hashed `base_step`, `lid_step`, `base_stl`, `lid_stl`, `review_glb`, original-byte `normalized_input_step`,
`source_step`, `request_snapshot`, and an identity-placement `assembly_manifest`. Optional `assembly_step`
is omitted because source reencoding fidelity was not established. The manifest preserves the complete
source STEP and both case parts in the same board coordinate system, providing the assembly without
rewriting the source PCB. `OPTIONAL_SOURCE_REENCODING_REJECTED` remains an explicit warning.

The real full-source GLB has **545 meshes**, **384,357 triangles**, and **43,367,816 bytes**: all 543 source
solids plus the two case parts. It uses the explicit absolute 0.25 mm / 0.4 rad display mesh. Its role remains
review only. The printable mesh legacy settings were preserved at the supplied 0.05 / 0.12 rad parameters;
CadQuery's legacy STL linear deflection is relative, so the `_mm` field alone does not establish an
independently measured absolute 0.05 mm mesh error bound.

The continuation does not publish or restore a print approval. Main-owner registration, independent
verification, slicing and browser review remain separate, and `print_hold` is preserved by the main owner.

`tests/cad/test_identity_recovery.py` uses real synthetic CAD artifacts with a clearly fault-injected
optional-export failure to test the continuation contract. It verifies fresh output and unchanged prior
failure, exact source byte identity and artifact hashes, and rejection of changed request/source/frame,
unrelated failures, missing required case checks, changed case solids and non-mm units. Targeted result:
**8 passed in 9.08 s**. This is a contract test, not evidence that the real reencoding volume drift has
been numerically diagnosed component by component.

### Automatic entry-point continuation

`generate()` now automatically attempts the same tightly scoped continuation **only after** writing an
immutable failed result whose sole failed check is `normalized_input_step_roundtrip`. The recovery policy
still independently requires an unchanged request/source hash, all required case checks, exact identity
frame and exclusively native-mm source units. On success it returns the fresh preserved-source result;
on ineligibility or recovery failure it returns the original failure and leaves an explicit progress note.
No other CAD, collision, protected-feature, source-validity or service-path failure can use this branch.
Nonidentity and non-mm inputs continue through the original true normalization path and fail if that
normalization cannot meet its fidelity check. No existing real result was edited or regenerated to add
this entry-point behavior.

`tests/cad/test_automatic_source_preservation.py` injects an actual optional-export I/O fault by writing
scaled STEP geometry, allowing the real roundtrip mass check to detect corruption. It verifies automatic
fresh-run source-byte recovery only for native-mm identity input; translated-mm and identity-meter inputs
retain their failed results. A genuine support-cut conflict also remains a failure. Targeted result:
**4 passed in 4.39 s**. The test does not mock the validity verdict or fabricate passing measurements.

Final combined CAD regression after the automatic continuation change:
`/home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/cad -q` → **74 passed in 35.42 s**.

## Compact fit and real prototype feedback (2026-10-26)

The user's first printed prototype demonstrated excess all-around space. New proposals can explicitly
choose `manufacturing.enclosure_fit: "compact"`; omission or `"legacy"` preserves the old geometry path.
This module does not itself change a previously confirmed request or silently relocate PCB holes.

`compact.fitted_component_bounds()` retains the PCB and every imported component bound, except an active
`external`/`maintenance` port with `component_solid_indices` may clamp those retained solids' **outward face
coordinate only** to the corresponding PCB edge. All other coordinates and every unreferenced solid remain
in the budget. Referencing the PCB as an exposed connector is rejected. `clearance_xy_mm` is then added
once to the fitted envelope, followed by the wall, with no entire lid-post diameter around all four sides.
The lid underside uses the same fitted positive-Z budget plus `clearance_top_mm`. Every source solid is
still checked with exact BRep intersections after openings; excluding a size contribution never excludes
a source solid from collision checking.

Four local lock ears are selected deterministically on one pair of opposite sides. A 25-slot per-side
search picks two separated positions near each side's ends while screening exact source and required
opening/keepout intersections. The chosen cylindrical ears join the original walls, keep the requested
blind pilots/counterbores, and do not enter the required cavity XY gap. No feasible layout returns a failure.
Every actual base and lid must remain a single valid positive-volume solid. PCB mounting coordinates and
support-hole primitives are unchanged. The ear layout is geometric only; its strength is unqualified.

Compact openings extend inward at least to the body's corresponding inner plane even when a source
connector's mating face already projects beyond the compact wall. This prevents an apparently exposed
connector from leaving a sealed wall behind its mating face. Existing service boxes and protected material
checks remain mandatory. Compact printable lids explicitly rotate 180 degrees about board X before
translation to the bed; each STL records the actual transformation. This is a print placement, not a
physical bridging/support qualification.

New metadata includes `body_outer_bounds`, `shell_wall_planes`, `actual_footprint_mm`,
`enclosure_fit` (mode, selected ear axis, fitted bounds and reservation), and `port_surface_access`.
`outer_bounds` in compact mode is the actual measured union bbox including ears, not the main body's
rectangle. `compact.body_and_footprint_budget()` is a pure helper for the orchestrator: it computes exact
body/cavity planes and lid contact height, and a conservative envelope containing either ear orientation
and any requested recessed panel. These bounds are not substitute collision evidence.

Optional per-active-port `surface_access` declares `max_recess_mm >= 0`. A declaration alone records a
construction-plane comparison and may be unmet; it cannot assert a flush physical interface. For a top
rectangular port, explicitly supplying `panel_thickness_mm` and `recess_margin_mm` additionally builds a
real lower annular panel with vertical return walls, preserving the original opening. The margin must be
at least the main wall and the panel must exceed ten length tolerances. This is supported only for
`z_max` rectangles in compact mode. Other recessed-panel directions/shapes remain unsupported.
`target_met_by_construction` is **only** plane bookkeeping; independent measurement of neighboring
actual shell material and real component travel/plug geometry is still required. Recess geometry cannot
remove required fastener material or hide a component collision.

Freeze part bounds/volumes before any STL mesher can add cached triangulations; display bbox drift cannot
contaminate authoritative geometry metadata. Optional STEP source-preservation recovery keeps compact
construction metadata rather than recomputing legacy dimensions, and freshly rechecks its case BReps.

Actual CAD-only checks so far:

- Full existing plus first compact regression: **79 passed in 40.81 s**, 15 dependency deprecation warnings.
- Expanded compact tests: **7 passed in 4.95 s** (reduced body and unchanged four mounting coordinates,
  protruding connector's real wall opening, local ear/pilot material, bed placement, real connected button
  recess, optional source-preservation metadata and pure dimension budgeting).
- Real source `3D_V821L2WBX_1_2026-10-24.step`, SHA-256
  `2b096e5a608d7089dbc6062cfb4fd410af0d810e17436928e0bbb47b45dd51cc`, all 543 solids retained:
  `data/cad-compact-20261026/real-plain/generated/cad-preserved-1_qwqdav/cad_result.json` is `ok` after
  required case geometry checks and native-mm identity source-byte preservation. Body dimensions are
  **104.9748006 x 64.9747193 x 17.0961571 mm**, actual footprint including local lock ears
  **104.9748006 x 76.9747193 mm**. The original legacy case was approximately
  123.4142778 x 85.2638202 x 22.0861604 mm. This plain draft does not yet meet all button flush targets.
- Real three-button recessed-panel trial deliberately remains **failed**:
  `data/cad-compact-20261026/real-button-wells/generated/cad-oelzdhac/cad_result.json` recorded
  `lid_input_nonpenetration` common volume **58.6350202102 mm3** with retained original component solids.
  Dense components conflict with the trial's walls/low panel. No collision, opening or clearance criterion
  was relaxed and no failed trial artifact was advertised as usable. A different open operating area or
  per-component contour is needed for this actual board.

These are offline case-generation observations. They do not establish arbitrary-board semantic
identification, complete assembly access, valid screen integration, strength, physical fit or print acceptance.

## Rear display packaging prototype (2026-10-26)

The user supplied a 110 x 70 x 3.2 mm screen and chose the PCB-back (-Z), long-edge-parallel layout.
`constraints.display` requires these explicit fields (all lengths mm): `id`, `source`, `size_mm`,
`orientation: "pcb_back_parallel"`, `clearance_xy_mm`, `clearance_z_mm`, `board_to_display_gap_mm`,
`pcb_bridge_thickness_mm`, `pcb_post_height_mm`,
`face_opening: {mode: "prototype_edge_ledge", ledge_width_mm}` and
`ribbon_channel: {face, center_tangent_mm, width_mm, height_mm, source}`.
The current prototype uses gap 4.0, XY/Z gaps .5/.5, bridge thickness 1.5, post height 2.0, a 1.0 mm
front edge ledge and a side x_max straight 40 x 2 mm ribbon reservation centered at board Y=30.
`manufacturing.standoff_height_mm` must explicitly match the 2.0 mm post height, and the PCB pilot depth
is explicitly reduced to 1.5 mm. The original PCB hole XY positions and source through-hole diameters
remain unchanged. Missing/unsupported display structure is rejected; it is not guessed from the photos.

`body_and_footprint_budget(..., display=...)` and `display.display_layout()` place the screen centered on
the actual PCB XY bounds. Its top is PCB underside minus the declared board-to-display gap. Display
width and height plus twice the XY gap join the needed board/component cavity extent. The cavity floor
is no higher than screen bottom minus Z gap. This legitimate screen volume is not accidental PCB slack.
The common helper returns the same dimensions used by the CAD kernel so orchestration can construct
consistent check/contact plans before generation, without changing the approved request after export.

The PCB is carried by four short original-axis posts supported by horizontal bridges to the nearest
outer wall. The bridges lie from PCB underside minus post height minus bridge thickness to PCB
underside minus post height. A declared gap must leave the requested screen Z clearance beneath them.
The posts do not descend through the screen. Blind pilots retain material below their bottoms; bridge
and post material is protected against all opening/channel cuts. Exact intersections with every retained
source solid and the screen envelope remain required, and every resulting case part must be one valid solid.
This structure has not been strength- or screw-length-qualified.

A -Z-facing rectangular window uses display width/height minus twice the prototype edge ledge. The
ledge top equals the screen bottom, allowing only zero-volume planar edge contact; the main floor
otherwise retains the declared lower Z gap. For the actual user dimensions, the nominal window is
108 x 68 mm. The active display area and bezel dimensions are unknown, so this can overlap active pixels
and MUST remain a prototype for review. No statement of full visible area, clamp pressure, retention or
physical screen acceptance follows from fitting the rectangular envelope.

The side ribbon channel is a real CSG cutter and is independently checked against source geometry.
Its axial interval reaches from one wall thickness inside the PCB edge to just beyond the body
exterior (specifically x_max starts at PCB maxX minus wall thickness); its tangential interval is center
plus/minus half the width, and its Z interval starts at screen top plus declared Z clearance and extends
upward by the explicit channel height. This is a static straight routing volume. It does not establish
actual FFC length, bend radius, connector engagement, flex assembly path or retention.

`metadata.display` records `envelope_bounds`, `seating_region_bounds`, `pcb_post_bounds`,
`pcb_bridge_bounds`, `face_opening_bounds`, `edge_ledge_bounds`, `ribbon_channel_bounds`, source and
explicit unverified active-area/FFC/retention flags. `screen_envelope_step` is a genuine box BRep made
from the user dimensions, labelled as a proxy rather than a manufacturer model. The review GLB includes
`screen-envelope_user-dimensions` alongside the unchanged original PCB/source solids and generated case.
Native-mm identity source-preservation recovery also copies and checks this proxy and includes it in
the fresh manifest. It never adds the proxy to the normalized original source geometry.

`constraints.open_operation_regions` optionally lists explicit box bounds and sources. These are actual
case cutters, preserve all support/fastener material and are copied into metadata. A shared open area
can expose adjacent controls without adding the failed trial's local well walls. It does not relocate
individual port requests, establish button travel or claim the buttons project above a distant lid face.

Offline display/operation tests: **6 passed in 6.24 s**. Combined CAD suite after compact and display
changes: **87 passed in 47.26 s**, 15 dependency deprecation warnings. Tests include screen/source/case
zero-volume penetration, real front aperture and ribbon void, original four-hole supports, short pilots
and bridge elevations, unsupported stack rejection, real open-operation removal and proxy preservation
through an intentionally corrupted optional source export. These are synthetic CAD checks; the actual
user request is separately generated and checked by the integration owner before review.

## Removable PCB carrier and screen-first assembly (2026-10-26)

The earlier integrated-bridge display draft is retained as static geometry evidence, **not** as an
assemblable delivery: the screen's +Z entry is blocked by its integral bridges and its -Z entry by the
smaller display-face window. Static nonpenetration alone did not establish a usable assembly path.

`display.support_structure: "removable_pcb_carrier"` explicitly changes the support to an auxiliary
third printed part while retaining the main `single_pcb_screw_lid` structure. Required new numeric
inputs are `carrier_clearance_xy_mm` (.3 in the prototype), `carrier_slot_clearance_mm` (.3),
`carrier_frame_width_mm` (2.0), and `carrier_clamp: "shared_lid_m3"`. The independently measured
original PCB holes remain the sole PCB mounting datum; supports now belong to `pcb_carrier`.

The carrier has a 1.5 mm planar perimeter frame/bridges at Z=-3.5..-2.0 and the original-axis short
2 mm PCB posts at -2.0..0. Its side ribbon segment is cut through the carrier as well as the base, so
it does not silently obstruct the declared FFC routing. The other frame sides and ends keep it a
single connected solid. Carrier XY edges retain the specified sliding gap from the base cavity.

Four shared M3 axes secure this auxiliary part, using the previous local-ear locations. Base ears and
pilots now end at the carrier's lower plane (-3.5). Carrier ears have true M3 clearance bores and seat on
those base ears; the lid has hollow downward spacers ending at carrier top (-2.0). Long lid screws clamp
lid spacer -> carrier ear -> base blind pilot. Carrier connecting arms enter through real top-open U slots
in the base wall; the lid's downward tongues fill the upper part of those slots, retaining the declared
sliding gaps. The carrier is therefore positively located and clamped, rather than left floating over the
screen. The proposed M3x16 length gives approximately 3.9 mm pilot engagement for the actual prototype
stack, but head shape, correct screw/thread type, torque and strength remain physically unqualified.

The resulting declared installation order is screen from +Z into the empty base, then carrier from +Z,
PCB/source assembly from +Z, and lid from +Z. The base has no PCB bridge or post crossing the screen entry
projection. Contact planes are base/carrier at -3.5, carrier/lid spacers at -2.0, carrier/PCB at 0, and
base/lid rim at the separate computed lid mating plane. These are finite annular or rim face contacts,
not permission for positive-volume interference or arbitrary whole-part contact exceptions.

`pcb_carrier_step` and `pcb_carrier_stl` are separate hashed artifacts; the review GLB has a named
`pcb_carrier` node. `metadata.hardware.pcb_mount_part` identifies the carrier. Display metadata includes
`carrier_bounds`, `carrier_access_slot_bounds`, `carrier_clamp_screw_centers_mm` and
`carrier_contact_planes_mm`. All three printable parts are BRep-validated as single solids, checked
against source/screen/other case parts and all required access voids, and independently placed on the
print bed. The same strict native-mm source-preservation recovery now requires the carrier checks and
copies its STEP/STL, source binding and assembly-manifest entry into the new result.

The pure budget helper additionally reports `lid_min_z_mm` (the downward clamp stem bottom, -2.0),
`pcb_carrier_z_bounds_mm` (-3.5..0) and `base_lid_fastener_top_z_mm` (-3.5), so the independent plan
cannot incorrectly use the flat lid's old 7.096 mm underside as its moving-geometry lower boundary.

Carrier-specific offline regression: **4 passed in 9.56 s**. It checks real carrier/pilot/clamp material,
all parts remain single solids after the routing cut, preservation through an actual deliberately damaged
optional STEP export, and the **continuous** +Z 30 mm BRep translation union of each installation stage
against the parts present at that stage. Every synthetic stage had zero common volume; this is not a
sampled-pose test, and the exact original hole voids remain in the sweep. The actual 543-solid user board
receives a separate fresh generation and independent verification under its immutable request hash.

Actual immutable removable-carrier request `d0ab2583a907400f9fa0d82ea79e7d17`:
request SHA-256 `bb61ffe869c4fc5f9153f105e6ce45146783950d6c278830abcfafe3a065524d`.
Actual result:
`data/jobs/d0ab2583a907400f9fa0d82ea79e7d17/generating/cad-preserved-2ylq4r88/cad_result.json`.
Generation is `ok`, **617 local checks pass, 0 fail, 4 not checked**. All 12 advertised artifacts were
freshly SHA-256-verified after completion. The 543 original source solids remain byte-preserved;
normalized source and raw source are the same 62,527,894 source bytes. The optional lossy STEP rewrite
failure remains in its separate prior run and is not advertised as usable output.

The main body is **115 x 75 x 19.7961571 mm**. The actual overall local-ear extent is
**115 x 87 x 19.7961571 mm**. The extra screen volume is explicit; lock ears still add 6 mm per Y end
and are not hidden by quoting only the body. Carrier volume is 1499.1589460 mm3; base/lid/carrier are each
one positive-volume valid solid. Carrier bounds are approximately [-5.499900,-13.499940,-3.5] to
[105.500100,73.500060,0]; lid's actual lower extent is -2.0 because of its hollow clamp spacers.
Four M3 axes exactly match the frozen prototype plan; the PCB support axes remain the four original
source positions. The screen envelope remains a user-dimension proxy, not a recovered detailed component.

No live printing, heat, motion or account operation was performed. The main owner must register and
independently verify this result, including screen-first/carrier/board/lid assembly stages. The current
side ports still have the disclosed global-wall recess in their construction metadata; screen active area,
FFC bend/length, detailed real plug engagement and physical fastening/fit remain unaccepted. No design
hold is removed by CAD generation alone. New CAD full regression including the carrier: **91 passed in
57.21 s**, 15 third-party dependency deprecation warnings.

## 2026-10-26 local side U bays and actual sliding-clearance correction

The old carrier prototype `d0ab...` was retained as failed-path evidence after the independent checker
found real non-ear sliding contacts at two interfaces: carrier arms touched the cavity's tangent wall,
and lid wall tongues retained only a 0.001 mm normal slot gap. This revision clips each connecting arm
to the declared carrier XY boundary and expands the wall slot by the existing explicit
`display.carrier_slot_clearance_mm` (0.3 mm here). It does not increase contact exemptions or reduce
source clearance. The circular M3 seating ears remain unchanged, and arm ligament must still meet the
existing wall-thickness minimum.

A side connector can now explicitly request the following `surface_access` dialect:

```json
{
  "mode": "side_u_bay", "mount_part": "lid",
  "max_recess_mm": 0.5, "recess_margin_mm": 1.5,
  "panel_thickness_mm": 0.8, "open_bottom_z_mm": 0.0,
  "sliding_clearance_mm": 0.3,
  "source": "caller-confirmed local prototype bay and mounting decision"
}
```

The original rectangular opening dimensions, source part, mating plane and entire service volume are
retained. A real thin local face panel joins the lid through two normal return walls. The panel and
returns enter a base receiving slot with the declared sliding gap. The lower edge is open, permitting
the PCB to be installed before the lid-mounted panel. This avoids a board-edge obstruction that a
permanent low base-mounted faceplate could create. The original opening also cuts through the deepest
backplane of neighboring same-face bays, so an adjacent return wall cannot obstruct its approach.

Overlapping same-face bay pockets form connected interval groups and share the lowest declared pocket
bottom. Otherwise a deeper neighboring bay can sever a tiny isolated return-wall fragment. The explicit
lower faceplate edge remains the individual `open_bottom_z_mm`; it is not silently moved to match the
other panel. `metadata.side_bays` exposes the merged IDs, actual panel/return/pocket/receiving-slot bounds,
plane, thickness and unverified plug/actuation scope for independent measurement.

All protected feature records now carry their owning printed part. Generic original openings, keepouts,
operating regions and ribbon cutters still protect every part. A bay pocket removes only base/lid stock,
and its guard therefore protects only those parts. A base receiving-slot cutter protects only base
stock. The separate carrier is never cut by these extra shell-only pockets. Its unchanged original
service envelope is still checked. A regression deliberately puts 6.3 mm3 of a carrier bridge below the
original service region inside the extra shell pocket: the carrier survives, original service remains
empty, and base/lid material inside the pocket is zero. This scopes a Boolean operation correctly; it
does not waive a collision or permit removal of carrier support.

The frozen actual request is job `95296a77728c46f798ac91d1cffbb8aa`, SHA-256
`f9a2d86dd02277ce6af4cc131821f047bb16ca2567109add205952b371ac4cdc`.
The three source-bound local bay parameters are USB panel 0.8/bottom 0, SD panel 1.0/bottom 2.686156972314,
and KEY panel 1.0/bottom 0 mm; USB/SD maximum local recess is 0.5 mm, KEY 0 mm. Existing confirmed apertures
are unchanged. Parent-owned semantics now classify the SMD buzzer opening as an acoustic vent rather
than an insertion connector; its opening stays unchanged and acoustic performance remains unverified.

Owner-only real merged CSG diagnostic:
`data/cad-compact-20261026/side-u-bay-probe-merged/probe.json` and
`independent_fit_probe.json`. Actual base/lid are each valid single solids. The unchanged original
aperture approaches contain zero shell volume; independent local material/collar measurements found
USB/SD recess 0.5 mm and KEY zero. Added lid stock has at least 1.1 mm board/source clearance in the
per-solid diagnostics. The main owner independently ran its actual BRep fit policy on this diagnostic:
10/10 passed (three local bays, three top operating accesses and four explicit screen-side allocations).
These diagnostics are not standalone job approval or physical fit acceptance.

The first frozen run `generating/cad-47g3plnt` remains failed evidence of the incorrect unscoped guard.
The corrected full run is `generating/cad-lpiqmv9d` and continues without editing the request or old
artifacts. For immediate user review, its already reimported three STEP checkpoints were copied into
`generating/review-prototype-2on8u39f/prototype_manifest.json`, with separately bed-normalized STL for
each part, user-sized screen STEP proxy, original byte-preserved source reference and actual SHA-256s.
This early manifest explicitly says `review-prototype`, `print_hold: true`, and complete independent
verification pending. It does not falsely advertise a completed CAD result. The complete actual review
GLB was also produced in the formal run (43,483,004 bytes; SHA-256
`631e14c2b5a99606a7406e5c87303e183002842ab3edbdb1eff04cc08e0d3cfb`).

Checks run after the part-scope fix: full CAD regression **94 passed in 76.50 s**. Then three additional
regressions checked untouched carrier-support preservation, unequal-depth overlapping-bay connectivity,
and actual noncontact sliding-face distance after removing only 0.01 mm slices at intentional horizontal
seats. The complete side-bay/carrier subset, including those three tests: **10 passed in 23.82 s**.
A final full regression and full actual job verification are recorded below after their completion.
No hardware or account operations were performed; screen active area, actual FFC bend, connector plugs,
button stroke, fastening strength and printed physical fit remain separate incomplete acceptance items.

Final full CAD regression after all side-bay and part-scope changes: **97 passed in 81.49 s**
(`tests/cad`, real CadQuery/OCP, no cache provider, `/tmp` fixture outputs). No failures were ignored.


Actual corrected full generation completed:
`data/jobs/95296a77728c46f798ac91d1cffbb8aa/generating/cad-preserved-5bfty5d_/cad_result.json`.
Result is `ok`: **620 local checks pass, 0 fail, 4 not checked**, with all **12 advertised artifact
SHA-256s freshly checked**. The strict original-byte preservation policy retained all 543 source solids
and the original 62,527,894-byte STEP (hash `2b096e5a608d7089dbc6062cfb4fd410af0d810e17436928e0bbb47b45dd51cc`).
The sole optional source STEP rewrite mismatch remains failed evidence in `cad-lpiqmv9d`.
Base/lid/carrier STEP hashes exactly match the early review checkpoints. The final STLs are the formal
in-memory-BRep exports; independently meshing the same STEP for the early preview produces different
mesh hashes, so approval must bind the formal final artifacts rather than substitute the quick mesh.

Actual repaired base/lid/carrier volumes are 12532.6823048 / 18972.6373410 / 1489.6569218 mm3;
each is one valid positive-volume solid. Body 115 x 75 x 19.7961571 mm and complete footprint including
local ears 115 x 87 x 19.7961571 mm remain explicit. Bay construction recesses are USB/SD 0.5 mm and KEY
0 mm, to be measured again by the independent checker on these exact output hashes. Full source/part
collision and assembly paths, display allowance and real near-flush BRep proof remain integration-owned
verification. The final small download package `compact-screen-review-v2.zip` contains the exact formal
STEP/STL/screen artifacts, CAD result and assembly manifest, with a README disclosing excluded large
source/GLB artifacts and incomplete acceptance. Neither CAD status nor this archive removes print hold.

## 2026-10-26 explicit KEY protrusion, smaller shell and bounded source reuse

`surface_access.min_protrusion_mm` is an optional nonnegative minimum signed protrusion at the original
static source mating plane. When explicitly supplied, the constructed local panel moves inward by
that amount along its face normal; the original PCB/button is not moved. Missing values retain the
old `max_recess_mm` construction behavior. Kernel local-panel cutters, neighboring-port backplanes,
metadata and top-panel budget all use the same offset. A target does not assert physical button stroke.

For the actual side black KEY, the requested 1.0 mm protrusion means local outer Y=62.276520264566 and,
with unchanged 1.0 mm panel thickness, inner Y=61.276520264566. The original window and service box are
retained. Its actual neighboring shell material must be independently measured again on the output;
open space or distant cover planes cannot substitute for a real local lip.

An explicitly chosen `lid_fastener.preferred_ear_axis: "y"` constrains the compact local-ear search to
the two Y ends. An infeasible choice returns failure, rather than silently choosing a longer X layout.
Omission keeps the old deterministic area heuristic. `display.carrier_clamp: "shared_lid_fastener"`
is the new dimension-neutral spelling, retaining the old `shared_lid_m3` compatibility value. Actual
clearance/pilot/head dimensions come from `manufacturing.lid_fastener`; reported prototype screw length
now reads its `screw_length_mm` rather than hardcoding 16 mm.

The smaller prototype uses wall/base 1.6 mm, lid 2.0 mm and local M2 ears Ø6.2 mm, pilot Ø1.6 x 4.5 mm,
clearance Ø2.2 mm, head counterbore Ø4 x 1.2 mm and nominal M2x14. Original four PCB axes, M1.6 PCB
hardware, 2 mm short posts, 4 mm board/display separation and 111 x 71 mm screen seat remain unchanged.
Ø6.2 leaves 0.1 mm extra geometric arm-ligament margin beyond clearance-radius + 1.6 mm wall after the
0.3 mm sliding trim. These dimensions are explicit prototype constraints, not strength/torque acceptance.

`board.metadata.source_export_mode: "native_identity_bytes"` explicitly avoids source reencoding:
require exactly identity board frame and exclusively native-mm STEP length units, then copy and hash
original bytes as `normalized_input_step`, publishing an identity assembly manifest for the actual
separate case/screen parts. No optional rewritten assembly STEP is advertised. Omitted mode retains
legacy reencoding and its strict failure/recovery policy. This removes the known multi-minute, lossy
94 MB source-rewrite attempt from the new explicit workflow without relaxing any case checks.

An optional trusted caller argument `generate(..., source_validation_cache={"result_path":..., "sha256":...,
"ocp_version":...})` can reuse only unchanged input-solid validity. It first freshly imports source BRep
and checks caller-pinned old CAD-result hash, old status `ok`, exact source and normalized artifact
hashes, same original source bytes, exact identity frame, native-mm units, same CadQuery/OCP versions,
and one complete passing validity check per inventory solid with matching bounds/volume/count. All
reused checks carry explicit old-result/source/runtime provenance. New case geometry, original PCB
mechanical constraints, open access, support protection, source/case nonpenetration and other case
checks still run against the actual freshly imported source; they are never inherited from the cache.
A bad descriptor/artifact/version causes structured failure before accepting reused checks.

New results record both CQ and OCP versions. The immediately prior result recorded only CQ. For that
one explicit same-venv unchanged-dependency continuation, the caller supplies the current OCP version
as an explicit runtime attestation; absence/mismatch rejects reuse, and output provenance discloses that
this version came from the caller instead of pretending it was in the old result. Old results are not
modified. The cache argument is execution provenance and never changes a frozen request.

Targeted regression covering the explicit panel/ear changes, byte-preserved source and constrained
cache: **14 passed in 14.54 s**. A changed required keepout still fails with a protected-feature conflict
after cached input validation; mismatching old-result hash, original/normalized artifact bytes and OCP
version are refused. Full CAD regression and actual new-job evidence are appended after completion.

Actual new immutable job `2845532654514a1398cc246d804c3fbb` completed using the explicit fast path:
`data/jobs/2845532654514a1398cc246d804c3fbb/generating/cad-9dsov066/cad_result.json`.
Frozen request SHA-256 `0e4fb56798d970da5cd405b492e2c1ce606707f938f345c8bfc0525e6f1a1de6`;
CAD result SHA-256 `5ad42e621b5093fd91d0cab0702f0847d197108234e6f0777d447098e6c8a735`.
Generation finished in about **65 seconds**, with no failed optional rewrite run: **617 pass, 0 fail,
4 not checked**. Of those passes, 543 are explicitly source-validity reuse with old-result/source/frame/
runtime provenance; newly generated case checks ran fresh. All 12 advertised artifact hashes were
freshly re-read and matched. Source and normalized source are the original byte-identical STEP.

Measured output body is **114.2 x 74.2 x 18.3961571 mm**, and overall ears make the footprint
**114.2 x 83.4 x 18.3961571 mm**. The explicitly pinned Y ear centers agree with the integration plan:
X=-2.399899669799/102.400100330201, Y=-8.599940309880/68.600059690120. The PCB's original four axes
remain unchanged. Base/lid/carrier are each valid single solids, volumes respectively
9718.2519284 / 12286.4540155 / 1376.3541311 mm3.

KEY construction metadata reports original static face Y=63.276520264566, actual constructed local
outer plane Y=62.276520264566 and signed protrusion +1.0 mm. This does not replace the integration
owner's final independent local-material BRep measurement. The neutral shared fastener metadata
correctly reports M2x14, rather than the previous hardcoded M3x16 label/length.

The actual complete GLB contains the original 543 board solids, all three case pieces and the screen
dimension proxy, 43,482,100 bytes, SHA-256
`00676ca9cc3889504b50bf96bb41d7f2abb74cd515b9d3163f4d47dd4bf5a5d8`.
The small formal download `compact-key1mm-review.zip` (421,930 bytes, SHA-256
`e0276832e367ff9d167df5253410e7fdbc1e65d92472df3eb5433655c7673830`) includes the exact three STEP/STL
parts, screen proxy, CAD result, assembly manifest and limitation README. Large original source,
GLB and request snapshot remain separately hash-referenced artifacts.

Final full CAD regression including legacy behavior, new explicit positive protrusion, M2 geometry,
pinned ear layout and bounded source reuse: **106 passed in 66.84 s**. No tests ignored or skipped for
this result. Main/integration still owns independent collision, rigid assembly path, actual local-lip
fit and display checks, artifact registration, review URL and print policy. No hardware action was
performed and print hold was not removed. Unknown real button stroke, screen active area, FFC bending,
plug fit, print calibration and fastening strength remain explicit acceptance limits.
