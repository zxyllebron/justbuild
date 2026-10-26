# justbuild collaboration rules

Read `docs/architecture.md` and your assigned file under `tasks/` before working.

## Confirmed product requirements

- Open-source, headless PCB enclosure generation framework, deployable on a cloud server.
- Primary input: a board STEP/STP assembly and a description of connector usage.
- Propose connector openings and maintenance access; the user confirms the proposal once before automatic design.
- Initial structure: one PCB, base and lid, screw fastening.
- Generate, check and slice automatically; the user reviews a browser page and explicitly starts printing.
- Review page: assembly, transparency, sections, risk locations and downloadable artifacts.
- Printer: Bambu Lab A1, across networks through the cloud, without assuming a site gateway or shared LAN.
- Core is open source. Optional printing adapters may use official components; a cloud VM is acceptable if necessary, subject to technical verification.
- Do not install SolidWorks or similar desktop CAD applications without discussing the need with the user. A headless Python geometry library is within scope.

## Compact fit and usable access (user physical feedback, 2026-10-26)

- Follow [the enclosure fit rules](docs/enclosure-fit-rules.md) for all new design proposals. New defaults explicitly select `manufacturing.enclosure_fit="compact"` and the versioned `fit_policy`; old confirmed requests and artifacts stay immutable and reproducible.
- Fit the cavity to the real PCB and necessary enclosed geometry. Prototype nominal side clearance is 0.5 mm per side; ordinary straight-wall gaps above 1.0 mm require a revised explicit rule/need. Never add a lid-boss diameter to every side. Fasteners, cable bends, plug/service volumes and local protruding parts must be handled locally with real geometric evidence.
- Keep original STEP dimensions, PCB mounting-hole centers/axes and required functional clearances. Never shrink the PCB/model uniformly or move a hole to obtain a smaller case.
- Buttons/switches must be exposed and operable from outside. An empty access hole with the control buried deep below it is insufficient. Measure static component protrusion relative to the actual local shell lip; use an explicitly modeled local recess, opening or other reviewed actuator solution when necessary. Static exposure does not prove stroke/force.
- External connector mouths must be near the local outer shell surface, with prototype maximum recess 0.5 mm, while keeping actual plug/locking/cable approach space clear. Maintenance-only internal connectors may use a declared cable exit/maintenance treatment instead; do not label an arbitrary service box as verified plug engagement.
- Missing surface datum, unresolved deep recess or missing geometry must remain required `not_checked`/`fail` and block print release under the new policy. CAD metadata and a rendering alone cannot satisfy these rules.
- A user-dimensioned screen/accessory is an explicit size constraint. The current rear screen is 110 × 70 × 3.2 mm, long sides parallel to the PCB; use a separately verified 111 × 71 mm accommodation region. This can legitimately set the body size while the PCB retains original-hole positive mounting. Report necessary accessory space separately from unexplained padding.
- Lowered PCB support posts must not pass through the screen or clamp its FFC. Use validated local bridges/short posts and explicit screw pilot depths. Screen active area/compressible bezel and actual cable bend geometry remain unresolved without data.
- A declared large open operation region may satisfy exposed-button access only when the full operating projection and exterior approach volume are independently empty in the exported shell BRep; do not present it as a button protruding above a distant cover.
- Keep the previous physical prototype as failure evidence. Revised parts need a new job/revision, source/constraint/artifact hashes and review; authorization of an earlier print does not authorize the revised design.

## Ownership when multiple chats share this directory

| Owner | Writable paths |
| --- | --- |
| 01 main / integration | `AGENTS.md`, `docs/architecture.md`, `tasks/`, `schemas/`, `pyproject.toml`, root configuration, `src/justbuild/` files outside other owners' directories, `web/`, integration tests |
| 02 CAD | `src/justbuild/cad/`, `tests/cad/`, `docs/cad.md` |
| 03 verification | `src/justbuild/verification/`, `tests/verification/`, `docs/verification.md` |
| 04 printing | `src/justbuild/printing/`, `tests/printing/`, `docs/printing.md` |

- Work only in assigned paths. Record proposed shared-interface changes in your own documentation for the main chat to integrate.
- Do not reset, clean, stash, overwrite, or revert another chat's work. Do not use broad staging or commits across owners' paths.
- Do not change shared dependency/environment files from a worker chat. List dependency requirements in your module documentation.
- Report completed work, exact files, checks actually run, known limits and required integration actions in your module documentation.
- Generated files belong under a caller-provided output directory. Use owner-specific temporary fixture directories during checks.

## Evidence and execution boundaries

- A rendering is not a collision check; a bounding box check is not a full assembly-path proof.
- Missing geometry, unknown connector clearance or absent thermal data must remain explicit. Never silently report unperformed checks as passed.
- All numeric geometry is in millimeters; report geometry tolerance and input provenance.
- Validate CAD solids; mesh rendering is for review, not the authoritative geometry.
- Geometry repairs cannot reduce user-required clearance, remove required openings or move mounting holes without a revised requirement.
- No live printer start, motion, heating, firmware changes or account changes while implementing adapters. The chosen product workflow requires an explicit review action before printing.
- Cloud telemetry/login success does not establish cloud print-dispatch capability. Verify upload, printer receipt, start acknowledgement and physical completion separately when authorized.
- Do not copy account credentials or proprietary signing keys into source, tests, artifacts or documentation.

## Current implementation status

This directory initially contained no source or Git repository. Version 0.1 now includes CAD generation, independent verification, HTTP/CLI orchestration, a browser review page and real local A1-profile slicing. See `docs/first-version-evidence.json` and `README.md` for actual checks. A1 cloud dispatch, production multi-user hosting and physical assembly acceptance are not verified.
