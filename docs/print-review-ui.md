# Exact-version print review UI

Updated 2026-10-24. This is an optional browser dialog for the existing `PrintController` contract. The module does not contact a printer, run a slicer or change a job hold by itself. API behavior is provided by the main application.

## Owned files

- `src/justbuild/web/print-review.js`
- `src/justbuild/web/print-review.css`
- `tests/integration/test_print_review.mjs`
- `tests/integration/test_print_review.py`
- `docs/print-review-ui.md`

The main chat owns `app.js`, `index.html`, `style.css`, `print_control.py`, server wiring and browser acceptance.

## Integration

```js
import {installPrintReview} from './print-review.js';
const printReview = installPrintReview({
  api,
  getJob: () => job,
  onRefresh: refreshSelected,
  toast,
});
// At the end of refreshSelected, after current job has been assigned:
printReview.update(job);
```

Include `/static/print-review.css`. Installation/update bind the existing `#print` button and explain its state in `#printnote`. The module creates exactly one native HTML `dialog` per document, lazily opens it on an explicit user action, and does not make requests during installation or update. Repeated installation returns the existing controller. The result exposes `update`, `onClick`, `close`, `dispose` and `dialog`.

Main should remove the old unconditional disabled-button/note handler or call `update(job)` after it. The browser's `api` wrapper must preserve same-origin ownership cookies and the supplied intent header. The module only constructs relative paths for the selected validated 32-character job ID.

## Explicit action contract

1. Clicking the main button calls `POST /api/jobs/{id}/print/prepare` with `{}`. It does not start printing.
2. A valid response must have `manifest`, `manifest_sha256`, status `awaiting_review` or `needs_review_hold`, and matching job identity. The UI checks real/non-simulation scope, session ownership binding, masked A1 identity, slice SHA-256, exact required input/configuration hashes, all six recognized print options, complete risks and a maximum 15-minute review lifetime.
3. The dialog displays the A1 model and masked serial suffix, firmware/transport, nozzle/material/plate, every exact print option, full 3MF SHA-256 and byte count, profile/input bindings and every backend risk message verbatim. Known risk IDs also have a Chinese label.
4. A single explicit risk/identity/version acknowledgment enables the separate **确认并开始打印** button only if all other gates still allow printing.
5. That button sends `POST /api/jobs/{id}/print` with only the exact `reviewed_manifest_sha256`, the complete exact manifest risk-ID list and `action: start_print`.
6. Any start request consumes the local UI attempt. A timeout/unrecognized reply becomes unknown. The UI never automatically resubmits; subsequent actions show the existing record and offer manual `POST /api/jobs/{id}/print/monitor` with `{}`.

All three request types use JSON and `X-Justbuild-Intent: print-review`. Browser origin/session authorization and all server checks remain authoritative. UI checks are defense in depth and cannot authorize a printer by themselves.

No periodic monitoring, retry, automatic start or configuration change is installed. The only timer disables an expired review. If preparation is needed again, the user closes and deliberately reopens the review to obtain and review a new ticket.

## Holds, sharing and unavailable configuration

Every truthy `job.print_hold` blocks prepare and start without changing the hold. If a hold and read-only access both apply, the note leads with **此设计仍待本版审阅，尚未发印**, then states that the link is for viewing only. It does not invite an ownership claim for an older design. `can_edit !== true`, a task not ready to print, a changed job/request/slice, an expired ticket and an already attempted/unknown/dispatching submission all block start.

A needs-input preparation response can show **连接打印机** only when `configuration_url` is exactly `/printer-setup`. Protocol URLs, external URLs, `//host` URLs and added query strings are never turned into links. No automatic navigation occurs.

Existing public state can be supplied as `job.print_receipt`, `job.print_dispatch` or `job.stage_results.printing.result`. The UI shows unknown/dispatching as requiring reconciliation and provides only a read-only refresh action. Local state remembers attempts during the page lifetime. Cross-reload reconciliation relies on the backend publishing its existing redacted receipt and enforcing its sealed ledger; a browser-only map is not a durable deduplication mechanism.

A milestone is shown as observed only when that exact milestone has `status: observed`; a later printing state does not infer missing upload/receipt/start evidence. Device-reported completion remains distinct from physical component/assembly acceptance.

## DOM safety and presentation

Manifest, risk, error and device strings are inserted with `textContent`. There is no `innerHTML`, dynamic executable markup, direct fetch, credential input, full serial field, auto-start or remote URL interpolation. The dialog follows the page's light green visual system, has one version/identity summary, a bounded scrollable risk list, collapsible source details, a sticky action row and mobile spacing. Native dialog close/escape/backdrop interaction remains available; closing does not retry or cancel a submitted printer operation.

## Checks actually run

- `node tests/integration/test_print_review.mjs`: **17 behavioral contracts passed** using only a mock API and minimal DOM.
- `/home/wujie/.cache/justbuild/venv/bin/python -m pytest -q tests/integration/test_print_review.py tests/integration/test_dimension_review.py`: **2 passed in 0.16 s**.
- Checks cover passive installation and singleton creation; read-only/hold/not-ready zero requests; hold explanation precedence; exact explicit prepare headers/body; no automatic start; all risk IDs and manifest binding; one explicit start; timeout/unknown state with manual monitor only; unknown/dispatching recovery; local-only setup link; 15-minute expiry; changed hold/ownership/task/request/slice; malformed identity/options/risks/configuration; XSS literals; duplicate-click exclusion and the distinction between device completion and physical acceptance.

These checks did not call live print endpoints, contact any printer or start any physical operation. Browser appearance and main/server integration are owned and verified separately by the main chat.
