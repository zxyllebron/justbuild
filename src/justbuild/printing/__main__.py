"""Printing worker CLI; live transport remains unsupported by default."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import (BambuCliSlicer, FakeSlicer, FakeTransport, cloud_capabilities,
               export_profiles, monitor_print, prepare_print, submit_print)
from .common import PrintError, envelope, read_json, record_error, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--request", type=Path, required=True)
    prepare.add_argument("--cad-result", type=Path, required=True)
    prepare.add_argument("--verification", type=Path, required=True)
    prepare.add_argument("--slicer", type=Path)
    prepare.add_argument("--slicer-sha256")
    prepare.add_argument("--slicer-version")
    prepare.add_argument("--timeout", type=float, default=300)
    prepare.add_argument("--simulate", action="store_true")
    submit = commands.add_parser("submit")
    submit.add_argument("--print-job", type=Path, required=True)
    submit.add_argument("--approval", type=Path, required=True)
    submit.add_argument("--ledger-dir", type=Path, required=True)
    submit.add_argument("--simulate", action="store_true")
    monitor = commands.add_parser("monitor")
    monitor.add_argument("--submission", type=Path, required=True)
    monitor.add_argument("--simulate", action="store_true")
    profiles = commands.add_parser("profiles")
    profiles.add_argument("--resources", type=Path, required=True, help="Existing resources/profiles/BBL directory")
    for name in ("machine", "process", "filament", "source-id"):
        profiles.add_argument("--" + name, required=True)
    capabilities = commands.add_parser("capabilities")
    for command in (prepare, submit, monitor, profiles, capabilities):
        command.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            if args.simulate and args.slicer:
                parser.error("Choose --simulate or --slicer.")
            slicer = FakeSlicer() if args.simulate else None
            if args.slicer:
                if not args.slicer_sha256 or not args.slicer_version:
                    parser.error("--slicer requires --slicer-sha256 and --slicer-version.")
                slicer = BambuCliSlicer(args.slicer, executable_sha256=args.slicer_sha256,
                                       version=args.slicer_version, timeout_seconds=args.timeout)
            path = prepare_print(args.request, args.cad_result, args.verification, args.output_dir, slicer=slicer)
        elif args.command == "submit":
            path = submit_print(args.print_job, args.approval, args.output_dir,
                                transport=FakeTransport() if args.simulate else None, ledger_dir=args.ledger_dir)
        elif args.command == "monitor":
            path = monitor_print(args.submission, args.output_dir,
                                 transport=FakeTransport() if args.simulate else None)
        elif args.command == "profiles":
            path = export_profiles(args.resources, args.output_dir, machine=args.machine,
                                   process=args.process, filament=args.filament, source_id=args.source_id)
        else:
            path = write_json(args.output_dir / "cloud_capabilities.json", cloud_capabilities())
    except (PrintError, ValueError) as exc:
        result = envelope()
        record_error(result, exc if isinstance(exc, PrintError) else PrintError("INVALID_CONFIGURATION", "Invalid CLI adapter configuration."))
        path = write_json(args.output_dir / "printing_error.json", result)
    print(path)
    status = read_json(path).get("status", "ok")
    return {"ok": 0, "needs_input": 2, "unsupported": 3, "failed": 1}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
