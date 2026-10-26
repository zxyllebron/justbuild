# Download-only sliced-file handoff core

Implemented on 2026-10-24 in `src/justbuild/handoff.py`. HTTP integration belongs to the main chat; this module makes no network, desktop, account or printer calls. A file handoff is not a print-start acknowledgement or print authorization.

The current synthetic reference must not be presented as fitting the user's V821 board. Following the newer request to inspect and redesign around the real board, no public handoff ticket was created by this worker for the old synthetic example.

## Interface

```python
from pathlib import Path
from justbuild.handoff import HandoffStore, HandoffError

store = HandoffStore(Path("data/handoffs"))

# Internal CLI caller: only unowned jobs permit owner_sha256=None.
created = store.create(manager, job_id, "https://downloads.example",
                       ttl_seconds=900)

# Owner-authenticated HTTP caller: authenticate/authorize the request first.
created = store.create(manager, job_id, "https://downloads.example",
                       owner_sha256=request.state.owner, ttl_seconds=900)

metadata = store.get(created["token"])
landing_metadata = store.landing(created["token"])
file_bytes = store.payload(created["token"])
```

`create` returns the raw token only to its caller. Disk tickets retain its SHA-256 and an HMAC seal over the entire record. The token contains 32 random bytes encoded as 64 lowercase hex characters; ticket filenames contain its hash, not the token. Record tampering, token substitution, changed file contents and expiry are rejected. Lifetime defaults to 15 minutes and cannot exceed one hour. The injected clock is for deterministic tests.

`get` and `landing` return only public metadata. They revalidate the actual artifact's size and SHA-256 on every call. `payload` similarly revalidates and returns immutable bytes, avoiding a later filesystem reopen by `FileResponse`. Default maximum file size is 128 MiB; downloads read at most the configured limit plus one byte into memory. This is an MVP memory bound, not a streaming implementation or production bandwidth quota.

Metadata fields include:

- `id`, `job_id`, `label`, `basename`, `sha256`, `bytes`, `size_bytes`.
- UTC ISO 8601 `created_at` and `expires_at`.
- `synthetic`, `printer_model`, and a bounded selection of `settings` from the real preparation result. A missing synthetic flag is `null`, not an assertion of real-board validity. Printer serial/account identity is omitted.
- `landing_url`: `/handoffs/{token}`.
- `download_url`: `/handoffs/{token}/file.gcode.3mf`.
- `manifest_url`: `/handoffs/{token}/manifest`.
- `permissions: ["download_sliced_3mf"]`, `grants_print_permission: false`.
- Desktop URI candidates and `desktop_uri_dispatch_verified: false`; native-client protocol behavior requires separate validation.

All generated HTTP URLs share the explicitly configured public origin. That origin must be HTTPS with no username, password, path, query, fragment, controls, encoded segments or backslashes. Valid explicit ports are supported. Local tests may explicitly construct `HandoffStore(..., allow_loopback_http=True)` for HTTP on `127.0.0.1`, `localhost` or `::1`; this never enables HTTP for private LAN or public hosts.

## Binding and authorization

Creation requires `ready_to_print`, a verification verdict of `pass`, preparation `status=ok`, preparation `simulation=false`, the same job ID, and exactly one `sliced_3mf` artifact. The artifact must also match the preparation result's `sliced_artifact` declaration. Its bytes must match the recorded SHA-256 before a ticket is written.

A truthy `job.print_hold` always rejects creation, including an otherwise fully prepared job. This prevents a version paused for renewed board inspection from being newly handed off. A handoff does not grant any print permission in the first place.

An owner-protected job requires an exact `owner_sha256` match. Omitting the owner is accepted only for unowned CLI-created jobs, enabling a trusted internal command to prepare a delivery. **HTTP must still use the existing write/owner authorization gate**. A read-only review URL, public handoff token or knowledge of an unowned job ID must not be promoted to permission to create a ticket, modify a design or start a printer.

Only the chosen `.gcode.3mf` is addressable. Absolute paths never appear in returned metadata or error messages. Internal record paths are HMAC-bound, constrained to the original job directory and opened component by component with `O_NOFOLLOW` so neither directory nor file symlink substitution can redirect a download. Nonregular files, oversized files and files changing during reading are rejected. The implementation targets the project's Linux server runtime and uses standard-library `dir_fd`/`O_NOFOLLOW` APIs.

Tickets are written to unique temporary files, flushed with `fsync` and atomically replaced. There is no in-place mutation of a valid ticket, so simultaneous creations and reads do not observe partial JSON. POSIX modes are requested for private files; removable filesystems that cannot enforce them need a protected local data directory for deployment. The random bearer token is never written to disk or logged by this module. Browser history, copied links and HTTP access logs are outside this module and must be configured appropriately.

## Main HTTP integration

Expose a separate GET-only router on the public handoff service, with only the landing page, manifest and exact file route listed above. Do not expose the main job API or a directory server through that origin. The main service creates a ticket only after owner authorization; the standalone public router reads existing tickets.

For the file response, return `Response(content=store.payload(token), media_type="application/octet-stream")` and a controlled `Content-Disposition` filename from the verified metadata. Set `Cache-Control: no-store`, `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff` on landing, manifest and download responses. Apply `no-referrer` to external launch links as well. Do not run ordinary URL-path access logs on these routes because the URL includes a bearer capability.

Catch `HandoffError` (a `ValueError`), `FileNotFoundError` and `OSError` at the HTTP boundary and return a generic unavailable response, without exception paths. Escape `label` and profile names with `html.escape` when rendering HTML; metadata is text, not trusted markup. Standard HTTP `GET`/`HEAD` may be supported by main; no public mutation or printer route is required. Browser deep links should be explicit user clicks and must not silently launch on page load.

## Official desktop protocol source

The main chat verified the official Bambu Studio source at commit `926a7192574bcb9b3a732e1ec59a46d79cb45466` (v02.08.02.61). In [`src/slic3r/GUI/GUI_App.cpp`](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/GUI/GUI_App.cpp#L1126), lines 1126–1129 accept the `bambustudio://open` prefix, URL-decode the complete argument, and split on `file=`. The generated Windows URI therefore percent-encodes the entire nested HTTPS download URL. The same source's [macOS handler](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/GUI/GUI_App.cpp#L7667), lines 7667–7681, accepts `bambustudioopen://` and decodes the nested URL.

In the public release, the Windows handler at lines 1134–1153 and macOS handler around lines 7684–7694 prompt for confirmation when a file is outside the source's trusted-site list. A justbuild URL may therefore require that native confirmation as well as a browser protocol-launch confirmation. This core does not suppress or bypass either dialog. These URI handlers import/download a file; they do not establish that a printer starts. A fully unattended workflow needs a separate, explicitly authorized dispatch adapter.

## Validation performed

Command:

```bash
/home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_handoff.py -q
```

Result: **55 passed**. One existing Starlette/httpx TestClient deprecation warning was emitted. Tests use bounded deterministic prepared-job fixtures; they do not reslice geometry or contact a real printer.

Coverage includes exact payload transfer and HTTP delivery, permission and owner separation, TTL and clock bounds, token syntax, HMAC-covered metadata alteration, file-content changes, parent/file symlink replacement, absolute and traversal escapes, size limits, URL injection, explicit loopback debugging, synthetic/profile metadata provenance and 24 concurrent atomic ticket creations followed by reloads. The fixture's byte payload stands in for an already validated slicer result; this layer delegates 3MF/G-code semantic validation to the existing `prepare_print` adapter.

No production/public deployment, native Bambu Studio launch, printer upload, printer receipt, start acknowledgement or physical print is claimed by these tests.
