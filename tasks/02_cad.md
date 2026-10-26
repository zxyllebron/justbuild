# 02 CAD enclosure generation

Read `AGENTS.md` and `docs/architecture.md`. Implement only the CAD-owned paths.

Goal: a headless parameterized base/lid generator for one PCB, with mounting supports, screw fastening, confirmed port openings and keepouts. Use STEP/STP as the authoritative input; CadQuery/OpenCascade is the initial direction.

Implement the documented `generate` entry point. Include an explicit synthetic PCB/connector fixture and an example request under `tests/cad/`. Synthetic dimensions must never be presented as measurements of the user's attached board. Provide genuine STEP/STL output when dependencies are available, and browser-compatible assembly output if practical. Validate generated solids.

First establish a constrained, documented board frame and supported opening/fastener primitives. Missing required geometry should produce `needs_input`; unsupported shapes should remain explicit. Do not claim automatic semantic identification of arbitrary imported STEP parts unless implemented and checked. Do not alter verification rules to make a generated case pass.

Write progress and handoff evidence to `docs/cad.md`: dependency requirements, files, invocation, actual checks, remaining limits and proposed shared-interface changes. Do not edit main-owned files or install desktop CAD software.
