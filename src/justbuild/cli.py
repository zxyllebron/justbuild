from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and Path(argv[0]).suffix.lower() in (".step", ".stp"):
        argv = ["easy", *argv]
    parser = argparse.ArgumentParser(prog="justbuild")
    sub = parser.add_subparsers(dest="command")
    easy = sub.add_parser("easy", help="中文向导：拖入板卡、填写需求、打开审阅链接")
    easy.add_argument("step", nargs="?")
    easy.add_argument("--requirements")
    easy.add_argument("--label")
    easy.add_argument("--port", type=int, default=18765)
    easy.add_argument("--data-dir", type=Path)
    easy.add_argument("--no-open", action="store_true", help="只输出链接，适合云端或SSH终端")
    serve = sub.add_parser("serve", help="Run headless API and browser review")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=18765)
    serve.add_argument("--data-dir", type=Path, default=Path("data/jobs"))
    inspect = sub.add_parser("inspect", help="Inventory STEP and propose unconfirmed constraints")
    inspect.add_argument("step", type=Path)
    inspect.add_argument("--out", type=Path, default=Path("proposal.json"))
    run = sub.add_parser("run", help="Run a confirmed request without any desktop UI")
    run.add_argument("request", type=Path)
    run.add_argument("--data-dir", type=Path, default=Path("data/jobs"))
    demo = sub.add_parser("demo", help="Generate and verify the explicitly synthetic reference board")
    demo.add_argument("--data-dir", type=Path, default=Path("data/jobs"))
    handoff = sub.add_parser("handoff-create", help="Create an expiring download-only desktop handoff")
    handoff.add_argument("job_id")
    handoff.add_argument("--public-origin", required=True)
    handoff.add_argument("--data-dir", type=Path, default=Path("data/jobs"))
    handoff.add_argument("--ticket-dir", type=Path, default=Path("data/handoffs"))
    handoff.add_argument("--ttl", type=int, default=900)
    handoff.add_argument("--allow-loopback-http", action="store_true")
    receiver = sub.add_parser("handoff-serve", help="Serve scoped desktop handoffs; never a printer controller")
    receiver.add_argument("--ticket-dir", type=Path, default=Path("data/handoffs"))
    receiver.add_argument("--host", default="127.0.0.1")
    receiver.add_argument("--port", type=int, default=18766)
    receiver.add_argument("--allow-loopback-http", action="store_true")
    receive = sub.add_parser("receive", help="Verify and cache one handoff; optionally request import in existing Linux Studio")
    receive.add_argument("handoff_url")
    receive.add_argument("--allowed-origin", required=True)
    receive.add_argument("--cache-root", type=Path, default=Path.home()/".cache"/"justbuild"/"receiver")
    receive.add_argument("--open-studio", action="store_true", help="Request file import only; never starts a print")
    receive.add_argument("--launcher", type=Path, help="Fixed absolute executable Studio launcher")
    receive.add_argument("--allow-loopback-http", action="store_true", help="Explicit local testing exception")
    intake = sub.add_parser("intake", help="Accept a STEP plus user requirements and return a browser review link")
    intake.add_argument("step", type=Path)
    intake.add_argument("--requirements", required=True, help="User-authored request, separate from attached STEP contents")
    intake.add_argument("--label", default="板卡外壳")
    intake.add_argument("--base-url", default="http://127.0.0.1:18765")
    intake.add_argument("--data-dir", type=Path, default=Path("data/jobs"))
    lan = sub.add_parser("printer", help="Account-free A1 LAN pairing status and read-only connectivity tools")
    commands = lan.add_subparsers(dest="printer_command", required=True)
    status = commands.add_parser("status", help="Read only local redacted pairing metadata")
    status.add_argument("--profile", type=Path)
    probe = commands.add_parser("probe", help="Probe only the named private IP for first-seen TLS certificates; never authenticate")
    probe.add_argument("host")
    probe.add_argument("--timeout", type=float, default=5)
    check = commands.add_parser("check", help="Read-only authenticated device identity, state and FTP login check")
    check.add_argument("--profile", type=Path)
    check.add_argument("--timeout", type=float, default=15)
    sub.add_parser("doctor", help="Check local package and asset inventory without device or model access")
    self_test = sub.add_parser("self-test", help="Generate a synthetic board and independently verify CAD offline")
    self_test.add_argument("--out", type=Path, required=True, help="New or empty output directory; never overwritten")
    sub.add_parser("capabilities")
    args = parser.parse_args(argv)
    if args.command in (None, "easy"):
        from .easy import run_easy
        raise SystemExit(run_easy(step=getattr(args, "step", None), requirements=getattr(args, "requirements", None),
                                  label=getattr(args, "label", None), port=getattr(args, "port", 18765),
                                  data_dir=getattr(args, "data_dir", None), open_browser=not getattr(args, "no_open", False)))
    elif args.command == "doctor":
        from .diagnostics import doctor_report
        result = doctor_report()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get("core", {}).get("status") == "ok" else 2)
    elif args.command == "self-test":
        from .selfcheck import run_self_check
        try:
            result = run_self_check(args.out)
        except ValueError as exc:
            parser.exit(2, str(exc) + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit({"pass": 0, "fail": 1}.get(result.get("verdict"), 2))
    elif args.command == "serve":
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port, access_log=False)
    elif args.command == "handoff-serve":
        import uvicorn
        from .handoff import HandoffStore
        from .handoff_server import create_handoff_app
        uvicorn.run(create_handoff_app(HandoffStore(args.ticket_dir, allow_loopback_http=args.allow_loopback_http)),
                    host=args.host, port=args.port, access_log=False)
    elif args.command == "receive":
        from .receiver import receive, ReceiverError
        try:
            receipt = receive(args.handoff_url, allowed_origin=args.allowed_origin, cache_root=args.cache_root,
                              open_studio=args.open_studio, launcher=args.launcher,
                              allow_loopback_http=args.allow_loopback_http)
        except ReceiverError as exc:
            parser.exit(2, str(exc)+"\n")
        print(json.dumps(receipt,ensure_ascii=False,indent=2))
    elif args.command == "handoff-create":
        from .handoff import HandoffStore
        from .orchestrator import JobManager
        manager = JobManager(args.data_dir)
        try:
            ticket = HandoffStore(args.ticket_dir, allow_loopback_http=args.allow_loopback_http).create(
                manager, args.job_id, args.public_origin, ttl_seconds=args.ttl)
            # This explicitly requested capability is the command's result,
            # not an ordinary server access log. Do not print the raw token.
            print(json.dumps({key:ticket[key] for key in ("landing_url", "expires_at", "sha256", "bytes")}, indent=2))
        finally:
            manager.pool.shutdown(wait=True)
    elif args.command == "printer":
        from .printing.lan import lan_profile_status, probe_lan_tls, check_lan_connection, LanError
        try:
            if args.printer_command == "status":
                result = lan_profile_status(path=args.profile)
            elif args.printer_command == "probe":
                result = probe_lan_tls(args.host, timeout=args.timeout)
            else:
                result = check_lan_connection(profile_path=args.profile, timeout=args.timeout)
        except LanError as exc:
            result = {"status": exc.status, "issues": [{"code": exc.code, "message": str(exc)}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get("status") in ("ok", "configured") else 2)
    elif args.command == "intake":
        from .intake import intake_step
        result = intake_step(args.step, args.requirements, data_dir=args.data_dir,
                             label=args.label, base_url=args.base_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "inspect":
        from .inspection import inspect_step
        from .contracts import write_json
        write_json(args.out, inspect_step(args.step.resolve(), "unconfirmed-import"))
        print(args.out.resolve())
    elif args.command in ("run", "demo"):
        from .orchestrator import JobManager
        from .contracts import read_json
        manager = JobManager(args.data_dir)
        if args.command == "demo":
            from .demo import create_demo
            job = create_demo(manager, wait=True)
        else:
            request = read_json(args.request)
            source = Path(request["board"]["step_path"])
            if not source.is_absolute():
                source = args.request.resolve().parent / source
            job = manager.create(source, request, args.request.stem)
            job = manager.confirm(job["job_id"], request, wait=True)
        print(json.dumps(job, ensure_ascii=False, indent=2))
        manager.pool.shutdown(wait=True)
        raise SystemExit({"failed":1,"geometry_failed":1,"needs_input":2,"review_incomplete":2,"unsupported":3}.get(job["state"],0))
    else:
        from .providers import capabilities
        print(json.dumps(capabilities(), indent=2))


if __name__ == "__main__":
    main()

