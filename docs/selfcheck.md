# Standard offline self-check

`run_self_check(output_dir: Path) -> dict` generates the standard **synthetic**
80 × 55 × 1.6 mm PCB assembly, creates its base/lid through the existing CAD
pipeline and runs the existing independent STEP verifier. It is a reproducible
software smoke test for another installation. It is not a claim that an
arbitrary uploaded board is semantically recognized, fits physically, slices
correctly or prints successfully on an A1.

## Running and interpreting it

The main-owned CLI wrapper uses:

```sh
justbuild self-test --out /absolute/path/new-self-check
```

The directly implemented Python API is:

```python
from pathlib import Path
from justbuild.selfcheck import run_self_check

report = run_self_check(Path('/absolute/path/new-self-check'))
assert report['verdict'] == 'pass', report
print(report['report_path'])
print(report.get('semantic_geometry_sha256'))
```

The destination must be an explicit **empty or nonexistent directory**. Files,
nonempty directories, symbolic-link destinations and paths traversing symbolic
links are refused with `ValueError` before changing their existing contents.
The function reserves its output directory against concurrent use; it never
clears, resets or deletes an old run. Choose a new path to try again.

The returned dictionary is also saved atomically as `report.json`. Generated
input, STEP/STL/GLB files and full CAD/verification reports remain under
`jobs/<job_id>/`. The summary contains:

- `status`: `ok`, `failed` or `needs_input`.
- `verdict`: `pass`, `fail` or `incomplete`. Only `pass` is a successful self-test;
  a CLI caller should return a nonzero exit status for either other verdict.
- `report_path`, `output_dir`, `job_path`, `job_id` and generated `artifact_paths`.
- Self-test `checks`, the independent verification verdict, required-check count
  and summary, fresh STEP `geometry` and four measured `mounting_holes`.
- `not_checked_scope`, copied from the actual independent report, and explicit
  `execution` fields identifying operations that did not run.
- `semantic_geometry` and `semantic_geometry_sha256` for quantitative comparison;
  package/backend versions, elapsed time and bounded failure issues.

Missing geometry dependencies return `needs_input` / `incomplete` with
`GEOMETRY_BACKEND_UNAVAILABLE`; the function does not install them. Other
pipeline, artifact or geometric failures remain nonpassing, with the generated
job and stage reports retained for investigation. This module adds no dependency
beyond the existing project CAD backend.

## Isolation and what is actually run

The code reuses `create_demo(manager, wait=True)` and the existing
`JobManager.create`, `confirm`, `run`, stage registration and independent verifier.
A private `JobManager` subclass only bootstraps this invocation's root directory,
thread pool, lock and an offline slicing object. Its `slicer` is always `None` and
`synthetic_settings` always returns `None`.

This avoids constructing normal `SlicingConfig`, which reads optional slicer
environment settings. Consequently even a fully populated or malformed
`JUSTBUILD_BAMBU_*` environment cannot enable Studio, parse official profiles or
execute a slicer during this self-test. The production environment, service,
module globals, other managers and installed applications are not modified.
The instance thread pool is shut down before the function returns.

The real orchestration may write a blocked slicing-preparation result after
verification. That is ordinary preparation validation with no configured slicer
or printing settings; **no slice operation runs and no G-code is generated**.
The expected final pipeline state is therefore `review_ready`, rather than
`ready_to_print`. A passing self-test does not convert that state into printable
or approved status.

No provider/model CLI, network connection, printer discovery, credentials,
account login, upload, MQTT start, heating or physical motion is part of the
self-test. Those operations are recorded as `not_run`, not silently passed.
It never reads or runs the large real V821 acceptance job.

## Checks and geometric comparison

A passing report requires successful CAD, independent verification `status=ok`
and `verdict=pass`, a nonempty required-check inventory in which every check is
`pass` or `not_applicable`, three passing independent solid-validity records and
four independently verified PCB mounting datums.

The self-check then rereads the on-disk CAD and verification reports and imports
the actual current input, base and lid STEP files. It validates the input hash
and base/lid artifact hashes. It measures:

- OCC B-rep validity, all positive solid volumes and solid counts: four source
  assembly solids, one selected PCB, one base and one lid.
- Current B-rep bounds, dimensions and mass-property volumes, including the PCB's
  analytical expected volume `80 × 55 × 1.6 − 4π × 1.5² × 1.6`.
- Actual internal full-cylinder axes and diameters for all four PCB Ø3 mm bores
  and corresponding base Ø2 mm pilots. Centers are fixed at `(5,5)`, `(75,5)`,
  `(5,50)`, `(75,50)` mm. Measured axis offsets must be within **1e-9 mm** of zero;
  the separate independent check still proves target bore interval/clearance.

For comparison, the semantic payload quantizes bounds and hole centers/diameters
at **1e-5 mm**, volumes at **1e-4 mm³**, and axis direction components at **1e-6**.
Its SHA-256 includes only these quantities and validity/count data; it excludes
job IDs, directories, timestamps, elapsed time and package metadata. Raw
STEP/GLB byte hashes are deliberately not used to compare installations, because
export headers and embedded paths can differ between equivalent runs.

The fingerprint is a compact regression signature for this fixture, not a proof
that two arbitrary solids are congruent. Different geometry can share bounds,
volumes and selected hole axes. Review the full measured values and independent
checks when signatures differ; never loosen manufacturing constraints to force
a matching fingerprint. Changes to the fixture or geometry implementation may
legitimately require a new documented baseline. Preserve the runtime/backend
versions when comparing machines.

## Measured baseline on 2026-10-26

Versions: justbuild **0.1.0**, CadQuery **2.6.1**, cadquery-ocp
**7.8.1.1.post1**. One standard sample was generated during the test run.

| Actual STEP part | Dimensions (mm) | Volume (mm³) | Solids |
| --- | --- | ---: | ---: |
| Input assembly | 82 × 55 × 9.6 | 9242.761065788301 | 4 |
| PCB | 80 × 55 × 1.6 | 6994.761065788307 | 1 |
| Base | 104 × 77 × 18.6 | 31198.245493992148 | 1 |
| Lid | 104 × 77 × 3 | 23806.099133547006 | 1 |

All four PCB axis offsets and all four matching base-pilot offsets were measured
as **0.0 mm**. The independent report had **38 required checks**, all satisfied (**36 pass, 2 not_applicable**).
The preserved unperformed categories were cable bend, nonlinear assembly, RF,
strength, thermal and complete wall-thickness analysis. The bounded software
self-test passed while these optional physical analyses remained unperformed.

Baseline semantic geometry SHA-256:

```text
91a86a0a841b595403eb58e96f9d6a4a0f46d27c2a4b51906460559239cdbe75
```

Self-test body elapsed approximately **2.842 seconds** on this workstation;
startup/import time depends on the machine. A slower machine is not a geometric
failure, and this timing is not a cloud capacity or printer performance claim.

## Tests and ownership

```sh
/home/wujie/.cache/justbuild/venv/bin/python -m pytest -q tests/integration/test_selfcheck.py
```

Result: **12 passed in 7.44 seconds**. A module-scoped fixture builds and verifies
one genuine sample; all dimensional, axis, artifact, scope and fingerprint tests
reuse it. Other tests replay failed/incomplete stage results without another
build, reject unsafe destinations without modifying their contents, and
simulate a missing backend. The genuine run prohibits network connections,
provider/slicer subprocesses and constructing environment-backed `SlicingConfig`,
while supplying deliberately malformed complete slicer environment settings.
It also asserts that the process environment is unchanged.

Owned files: `src/justbuild/selfcheck.py`,
`tests/integration/test_selfcheck.py`, this document. The main owner integrates the
CLI command and distribution instructions. Existing CAD, independent verifier,
printing, orchestration, service and frontend sources are unchanged.
