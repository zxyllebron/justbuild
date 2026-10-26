# 04 Bambu A1 cloud slicing and printing adapter

Read `AGENTS.md` and `docs/architecture.md`. Implement only the printing-owned paths.

Goal: isolate slicing, review-ready print jobs, explicit approval, cloud transport and monitoring. Target Bambu A1 across networks without a site gateway or shared-LAN assumption. Core must remain open source; optional official components and a cloud VM bridge are allowed if necessary. This does not establish that any particular headless cloud path already works.

First inspect current primary official documentation and relevant implementation source. Distinguish cloud telemetry/login from a complete model upload and print dispatch. Document firmware, account-region and authorization dependencies without requesting or recording passwords/tokens/signing keys.

Implement the documented `prepare_print` and `submit_print` entry points with a pluggable slicer/transport interface. A fake transport may be used for local contract checks only and must clearly label simulation. Unsupported live capabilities must return `unsupported` rather than fabricated success. Do not start a real print, move/heat the A1, update firmware or change accounts. Respect the explicit approval tied to the final sliced artifact hash. Handle ambiguous submission outcomes without blind retries.

Prefer a real CLI slicer adapter when available, with pinned A1/nozzle/material/process profiles. Do not assume that a slicing CLI includes cloud sending. Evaluate official-component/VM fallback with evidence; document unverified claims as such. Do not install heavy software or shared dependencies; list requirements for the main chat.

Write progress and handoff evidence to `docs/printing.md`: sources, compatibility matrix, implemented versus unverified capabilities, tests, dependencies and integration actions. Do not edit main-owned files.
