# Exact-version print control

`src/justbuild/print_control.py` connects a browser's authenticated review action
to the existing `submit_print` durable ledger and opt-in `A1LanTransport`. It is
candidate LAN integration code, **not hardware acceptance or cloud dispatch**.
No printer was contacted, uploaded to, started, heated or moved in these checks.
The real V821 design remains on its existing review hold.

## Stable API and main-owner integration

```python
from justbuild.print_control import PrintController

controller = PrintController(manager, profile_path=None, state_dir=None)
review = controller.prepare(job_id)
result = controller.submit(
    job_id,
    reviewed_manifest_sha256,
    owner_sha256,
    acknowledged_risks,
    approval=None,
)
status = controller.monitor(job_id, owner_sha256)
```

All methods return public dictionaries with `schema_version`, `operation`,
`job_id`, `status`, `issues`, `retry_allowed:false` and
`dispatch_verified:false`. They do not return private files, absolute paths,
access codes or full printer serials. Errors are converted to bounded public
messages; vendor exception payloads do not become public messages.

**The HTTP owner must derive `owner_sha256` from the authenticated owning session,
never from request JSON or a read-only share token.** Authenticate the job owner,
validate CSRF/origin and require an explicit user start action before calling
`submit`. Calling this Python method is the trusted review-action boundary; its
optional JSON `approval` is an additional consistency check, not independent
proof that a human clicked. No background workflow or polling task may call it.
`prepare` has no ownership argument, so its HTTP route must perform the owner
check before calling it too. A CLI-created job with no owner cannot be claimed
by this module; the main owner must implement any future explicit ownership
migration separately.

The main owner controls HTTP status mapping, session handling, the review
checkboxes and dialog. Render all manifest data as text. The start request sends
`reviewed_manifest_sha256` and the complete list of acknowledged risk IDs. If an
optional `approval` object is used, it must contain `approved:true`,
`action:"start_print"` and the same `reviewed_manifest_sha256`.

`prepare` returns:

- `needs_input` and `configuration_required:"local_printer_pairing"`,
  `configuration_url:"/printer-setup"` when private pairing/runtime prerequisites
  are incomplete. It never returns the private profile or access code.
- `awaiting_review` with `manifest` and `manifest_sha256` for a current verified
  real slice and complete local pairing prerequisites.
- `needs_review_hold` with the same review fields when `job.print_hold` is truthy.
  This permits viewing the review but does not authorize starting.

`submit` unconditionally rejects a current `print_hold` for a new attempt. It
never clears or modifies that field, including when an optional explicit
approval object is supplied. The module also does not update any other job
state. Hold removal requires a separate main-owned review action and policy;
that action is deliberately not implemented here. An existing attempt can be
returned again without dispatching, including after a later hold or expiry.

## What preparation checks, without networking

`prepare(job_id)` reads local files only. It does not instantiate a transport,
perform discovery, connect to MQTT/FTP, upload or issue a printer command.
`network_access_performed:false` is returned explicitly. The optional paho
runtime is checked through the LAN owner's local `lan_profile_status`.

Preparation requires the existing `ready_to_print` job and registered on-disk
stage results to agree exactly with their in-job copies. It checks:

1. Confirmed constraints; successful non-simulated CAD; independent verification
   with status `ok` and verdict `pass`; no required failed/unperformed checks;
   real, non-simulated slicing in `awaiting_review` state.
2. Current request, CAD result and verification hashes against the slice inputs,
   and independent verification's own request/CAD hash bindings.
3. The original request's board STEP against `job.input_sha256` and
   `cad.metadata.input_provenance.step_sha256`; the CAD request provenance hash.
4. **Every declared CAD artifact** against its SHA-256, including source STEP,
   base/lid STEP/STL, normalized source and review GLB where declared. Base/lid
   STEP, base/lid STL and consumed source STEP are mandatory. The consumed source
   snapshot must match the original source STEP hash. Paths must remain inside
   this job, be regular files, traverse no symbolic links/parent traversal and
   remain within the 128 MiB per-file limit.
5. Optional registered `review_model` JSON and every derivative artifact, so
   changing the optimized browser model also invalidates the review version.
6. Exactly one declared/hash-bound real sliced 3MF, identical to
   `sliced_artifact`; valid ZIP CRC and exactly `Metadata/plate_1.gcode`; both
   original CAD model hashes matching the sliced model snapshots. This is
   container/provenance validation, **not independent toolpath safety proof**.
7. A1/Textured PEI profile scope. The LAN owner's public option validator accepts
   exactly five explicit booleans (`bed_leveling`, `flow_calibration`,
   `vibration_calibration`, `timelapse`, `use_ams`) and `bed_type`. Initial settings
   are leveling true, all other booleans false, and `bed_type:"textured_plate"`.
   AMS and arbitrary command/options injection are unsupported.

The existing slicer's reference printer identity is explicitly rebound in a
private prepared-job copy to the locally paired A1. The public review shows the
actual masked printer identity, identity hash, firmware, selected settings,
options and sliced hash. The original request and prepared slicing job remain
unchanged. This explicit review binding is required even for files originally
sliced against an unbound A1 reference profile.

The manifest binds the original source, all CAD artifacts, optional display
model, verification, source print job, exact sliced bytes, exact options and the
new private bound print-job hash. Public settings use a whitelist; extra private
settings fields are not forwarded. It lists the prototype transport uncertainty,
unobserved device/material state, absent toolpath safety proof, optional
verification gaps and a synthetic-model warning when applicable. All exact risk
IDs must be acknowledged; missing, extra or duplicate IDs fail.

## Private tickets and submission

The default private directory is
`~/.local/state/justbuild/print-control`, mode 0700. Use a private POSIX filesystem,
separate from public job downloads; `state_dir` and `profile_path` are trusted
server configuration, never HTTP parameters. Review records, signing key and
prepared authorization files use mode 0600. State paths reject symbolic links.
Atomic writes and directory fsync preserve records. No new dependency is added.

Each review includes a random 128-bit review ID and expires after at most 15
minutes. Its filename is the SHA-256 of the canonical public manifest. A private
server key HMAC-seals the whole ticket, including the full bound printer serial,
profile byte hash and private prepared job. The full loaded profile object,
including access code, is never copied into the ticket. Ticket tampering cannot
change the target, options or file while retaining the reviewed manifest hash.
Private records with full serials are not downloadable artifacts.

Submission checks the owning session, ticket owner, manifest integrity, expiry,
hold, exact acknowledged risks and current input/profile hashes again. Both
submission and monitoring pass `expected_profile_sha256` to `A1LanTransport`.
The LAN owner verifies and parses one exact profile byte snapshot before use;
changing pairing after review cannot silently redirect the operation. Preparation
also uses `load_lan_profile(expected_sha256=...)` to close its own metadata/hash
read race.

A job-level lock and private controller attempt record are written **before** the
existing core submission boundary. Core submission validates a short-lived
approval against this exact private print job/printer/slice, snapshots the bytes,
then uses its persistent shared ledger. Its approval callback checks current
ownership, hold, profile hash and expiry again. The transport itself must observe
a matching fresh printer identity/firmware and idle state before upload and
again before a start request. Those are future live actions after authorization,
not evidence obtained by preparation.

The controller's one-attempt-per-job record is stricter than the existing
job/printer core key: a new manifest or printer cannot create another attempt
for the same job. Repeating the same request returns its existing receipt.
Timeout, process interruption or another ambiguous outcome remains `unknown`
with reconciliation required. It is never automatically retried. A new review
manifest is not a retry token. Preparing another review is currently allowed,
but attempting to submit it after an existing attempt is refused.

Public receipts preserve observed `state`, milestone evidence and
`reconciliation_required`. `external_side_effects_possible` explicitly marks
attempted/ambiguous side effects; it is not a claim that a command arrived or a
print started. A broker publish acknowledgement is not a device receipt. Device
start acknowledgement, correlated printing telemetry, reported completion and
physical completion remain separate. The core's physical-completion milestone
remains unobserved without real acceptance evidence.

`monitor(job_id, owner_sha256)` only reads the prior attempt and invokes the
existing `monitor_print`/transport `poll`; it never calls submit or retries.
Expired review authorization does not prevent reading an existing submission.
A changed owner or pairing blocks monitoring through the wrong identity.

## Actual verification and handoff

Command:

```sh
/home/wujie/.cache/justbuild/venv/bin/python -m pytest -q tests/integration/test_print_control.py
```

Result: **41 tests passed**. The tests use real `submit_print`/`monitor_print`
ledger code with non-network transport doubles and harmless non-executed fixture
G-code. `socket.create_connection` is prohibited. They exercise current file
hashes, mandatory geometry provenance, each source/base/lid STEP/STL mutation,
changed display model, identity/profile pinning, ownership/share rejection,
unowned CLI jobs, incomplete verification, simulations, holds before and at the
core approval callback, 15-minute expiry, HMAC tampering, options, all risk IDs,
concurrent duplicate submissions, durable unknown outcomes and read-only polling.
Neither runtime GUI state nor actual credentials enter these tests.

A separate read-only check used the **real V821 job**
`019d8f0d6d92426aa96618210deb469a` with a temporary fake private profile, temporary
controller state and socket connections prohibited. All current source/CAD/
review/slice hashes were read successfully in approximately **0.634 seconds**;
the result was `needs_review_hold`, `print_hold:true`,
`network_access_performed:false`. No real job file or hold was changed.

Owned files: `src/justbuild/print_control.py`,
`tests/integration/test_print_control.py`, this document. HTTP/CSRF/session/UI
integration belongs to the main owner. Default unsupported cloud transport is
unchanged. This LAN controller only works where the server can reach the paired
printer; it does not meet the final across-networks/cloud product goal by itself.
A1 protocol compatibility, actual file receipt/start and physical print/assembly
acceptance remain unverified.

## Removable PCB carrier revision (2026-10-26)

For explicit `display.support_structure="removable_pcb_carrier"`, print preparation and the review manifest require `pcb_carrier_step`, `pcb_carrier_stl` and `screen_envelope_step` in addition to the legacy required CAD evidence. The slicer model inventory must contain exactly the bound base, lid and carrier STLs; two-model legacy jobs remain unchanged. Every model is snapshotted with its hash before slicing, and the actual 3MF slice inventory must contain all three objects on its single plate. This is provenance and complete-part validation, not physical printing acceptance.

The existing AMS Lite prototype now also permits one explicitly reviewed PLA tray (`use_ams=true`, `ams_mapping:[0]`); actual loaded-tray state is rechecked before upload/start. It does not infer a tray from the account or Studio. Non-AMS defaults are preserved. The older unsupported-AMS statement above describes the initial adapter; this narrow single-tray extension does not establish multi-material or cloud dispatch support.

The compact screen design remains held for review and missing physical evidence. No live print is authorized by this code integration.
