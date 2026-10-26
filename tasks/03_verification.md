# 03 independent geometry verification

Read `AGENTS.md` and `docs/architecture.md`. Implement only the verification-owned paths.

Goal: independent, machine-readable checks for a generated base/lid and imported PCB assembly. Implement the documented `verify` entry point without waiting for the CAD worker: construct synthetic fixtures under `tests/verification/` and consume the artifact contract.

Prioritize valid solids, unintended board/component versus shell intersections, clearance thresholds, mounting alignment, required openings and supported rigid linear insertion/assembly envelopes. Distinguish intentional support/fastener contacts from collisions. Report any unimplemented wall-thickness, cable, thermal, RF, strength or path analysis as `not_checked`; do not use rendering or a bounding box to claim a more precise check.

Include meaningful passing and failing geometry cases and incomplete-input cases. Report tolerances and measurements. Return bounded repair proposals; do not modify CAD-owned code or relax hard constraints.

Write progress and handoff evidence to `docs/verification.md`: actual methods, dependency requirements, checks run, limits and integration actions. Do not edit main-owned files.
