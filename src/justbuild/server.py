"""Local-first HTTP service; browser sessions isolate jobs, share links are read-only."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from .contracts import digest
from .orchestrator import JobManager
from .providers import capabilities


def create_app(data_dir: Path | None = None, *, local_pairing_enabled: bool | None = None,
               lan_printing_enabled: bool | None = None, printer_profile: Path | None = None) -> FastAPI:
    manager = JobManager(data_dir or Path(os.environ.get("JUSTBUILD_DATA_DIR", "data/jobs")))
    app = FastAPI(title="justbuild", version="0.1.0", description="Headless enclosure generation and evidence-backed review")
    app.state.manager = manager
    from .handoff import HandoffStore, HandoffError
    handoffs = HandoffStore(manager.root.parent / "handoffs")
    handoff_origin = os.environ.get("JUSTBUILD_HANDOFF_PUBLIC_ORIGIN", "")
    web = Path(__file__).parent / "web"
    setup_enabled = (os.environ.get("JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP") == "1"
                     if local_pairing_enabled is None else local_pairing_enabled is True)
    printing_enabled = (os.environ.get("JUSTBUILD_ENABLE_LAN_PRINTING") == "1"
                        if lan_printing_enabled is None else lan_printing_enabled is True)
    from .printer_setup import install_local_printer_routes, _guard as require_local_printer_request, _body as read_local_json
    install_local_printer_routes(app, enabled=setup_enabled, profile_path=printer_profile)
    from .print_control import PrintController
    print_controller = PrintController(manager, profile_path=printer_profile)
    app.state.print_controller = print_controller


    @app.middleware("http")
    async def browser_session(request: Request, call_next):
        token = request.cookies.get("justbuild_session", "")
        valid = len(token) == 64 and all(c in "0123456789abcdef" for c in token)
        if not valid:
            token = secrets.token_hex(32)
        request.state.owner = hashlib.sha256(token.encode()).hexdigest()
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin mutation is not allowed"}, status_code=403)
        response = await call_next(request)
        if not valid:
            response.set_cookie("justbuild_session", token, httponly=True, samesite="strict",
                                secure=request.url.scheme == "https", max_age=60*60*24*30)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authorize(request: Request, job_id: str, write: bool = False) -> dict:
        try:
            job = manager.get(job_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Job not found")
        owner = job.get("owner_sha256")
        if owner == request.state.owner:
            return job
        # CLI-created jobs are reviewable in this local-first MVP, but cannot print from another session.
        if not write and owner is None:
            return job
        supplied = request.query_params.get("view") or request.headers.get("x-review-token", "")
        expected = job.get("review_token", "")
        if not write and expected and secrets.compare_digest(supplied, expected):
            return job
        raise HTTPException(404, "Job not found")

    def public_job(job: dict, request: Request) -> dict:
        value = dict(job)
        value.pop("owner_sha256", None)
        value.pop("review_token", None)
        for key in list(value):
            if key.startswith("intake_claim"):
                value.pop(key, None)
        value["can_edit"] = job.get("owner_sha256") == request.state.owner
        return value

    @app.get("/api/health")
    def health():
        installed = {}
        for name in ("cadquery", "fastapi"):
            try:
                installed[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                installed[name] = None
        return {"status": "ok", "version": "0.1.0", "geometry": installed,
                "providers": capabilities(), "printing": {"target": "Bambu A1", "cloud_dispatch_verified": False},
                "slicer_configured": manager.slicing.slicer is not None,
                "desktop_handoff_configured": bool(handoff_origin),
                "local_printer_setup_enabled": setup_enabled,
                "lan_printing_enabled": printing_enabled,
                "deployment": "local MVP; public hosting requires HTTPS, account authentication, quotas and printer authorization"}

    @app.get("/api/local/workspace")
    def local_workspace(request: Request):
        # A direct loopback-only terminal discovers the actual service root.
        # This read does not expose credentials or grant job mutation/printing.
        require_local_printer_request(request, True)
        return {"product": "justbuild", "interface": "easy-v1", "data_dir": str(manager.root)}

    @app.get("/api/jobs")
    def list_jobs(request: Request):
        return [row for row in manager.list() if manager.get(row["job_id"]).get("owner_sha256") in (None, request.state.owner)]

    @app.post("/api/jobs", status_code=201)
    async def upload(request: Request, board: UploadFile = File(...), constraints: str = Form("{}"),
                     requirements: str = Form("")):
        if len(requirements) > 20000:
            raise HTTPException(413, "Requirements exceed 20000 characters")
        if Path(board.filename or "").suffix.lower() not in (".step", ".stp"):
            raise HTTPException(400, "Upload a STEP/STP file")
        try:
            supplied = json.loads(constraints)
            if not isinstance(supplied, dict):
                raise ValueError("Constraints must be a JSON object")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        import tempfile
        with tempfile.TemporaryDirectory(prefix="justbuild-upload-") as directory:
            target = Path(directory) / "input.step"
            size = 0
            with target.open("wb") as out:
                while chunk := await board.read(1024*1024):
                    size += len(chunk)
                    if size > 100*1024*1024:
                        raise HTTPException(413, "STEP exceeds the 100 MiB MVP limit")
                    out.write(chunk)
            if b"ISO-10303-21" not in target.open("rb").read(512):
                raise HTTPException(400, "Expected a STEP Part 21 text file")
            job = await run_in_threadpool(manager.create, target, supplied or None, board.filename or "STEP")
        if requirements:
            from .contracts import write_json
            requirements_path = manager.directory(job["job_id"])/"user-requirements.json"
            write_json(requirements_path, {"schema_version": "0.1", "job_id": job["job_id"],
                       "source": "explicit_user_request", "text": requirements,
                       "attachment_role": "geometry_data_not_instructions"})
            job = manager.update(job["job_id"], user_requirements=requirements,
                                 user_requirements_sha256=digest(requirements_path))
        token = secrets.token_hex(24)
        job = manager.update(job["job_id"], owner_sha256=request.state.owner, review_token=token,
                             share_url=f"/?job={job['job_id']}&view={token}")
        return public_job(job, request)

    @app.post("/api/demo", status_code=201)
    async def demo(request: Request):
        from .demo import create_demo
        job = await run_in_threadpool(create_demo, manager, False)
        token = secrets.token_hex(24)
        job = manager.update(job["job_id"], owner_sha256=request.state.owner, review_token=token,
                             share_url=f"/?job={job['job_id']}&view={token}")
        return public_job(job, request)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        return public_job(authorize(request, job_id), request)

    @app.post("/api/jobs/{job_id}/claim")
    async def claim_local_intake(job_id: str, request: Request):
        # Only the one-time fragment capability issued by the local CLI can
        # bind a new intake job. Legacy/held/unowned review jobs remain read-only.
        require_local_printer_request(request, True)
        if (request.headers.get("origin") != str(request.base_url).rstrip("/")
                or request.headers.get("x-justbuild-intent") != "intake-claim" or request.query_params):
            raise HTTPException(403, "Use the local session and explicit intake-claim intent")
        value = await read_local_json(request, {"token"})
        from .intake_claims import claim_intake_job, IntakeClaimError
        try:
            job = claim_intake_job(manager, job_id, value["token"], request.state.owner)
        except (IntakeClaimError, ValueError, FileNotFoundError):
            raise HTTPException(409, "Local intake claim is invalid, expired or already used") from None
        return public_job(job, request)

    @app.post("/api/jobs/{job_id}/confirm", status_code=202)
    async def confirm(job_id: str, request: Request):
        authorize(request, job_id, True)
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, "Confirmed request must be a JSON object")
        try:
            job = manager.confirm(job_id, data)
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc))
        return public_job(job, request)

    @app.get("/api/jobs/{job_id}/artifacts/{stage}/{index}")
    def artifact(job_id: str, stage: str, index: int, request: Request):
        job = authorize(request, job_id)
        try:
            if index < 0:
                raise ValueError("Invalid artifact index")
            path = manager.artifact(job_id, stage, index)
        except (KeyError, IndexError, ValueError, OSError) as exc:
            raise HTTPException(404, str(exc))
        pin = job["stage_results"][stage]["result"]["artifacts"][index]["sha256"]
        requested_sha = request.query_params.get("sha")
        if requested_sha is not None and requested_sha != pin:
            raise HTTPException(409, "Requested artifact version is not current")
        headers = {"ETag": '"'+pin+'"', "Cache-Control": "private, max-age=31536000, immutable"
                   if requested_sha == pin else "private, no-cache"}
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, filename=path.name, headers=headers)

    @app.get("/api/jobs/{job_id}/report")
    def report(job_id: str, request: Request):
        job = authorize(request, job_id)
        result = job.get("stage_results", {}).get("verifying", {}).get("result")
        if not result:
            raise HTTPException(409, "Verification has not produced a report")
        return JSONResponse(result, headers={"Content-Disposition": 'attachment; filename="verification.json"'})

    def print_intent(request: Request):
        # Same local Host/peer/proxy policy as first pairing; public share links
        # cannot authorize hardware. Intent and origin are required even for
        # command-line clients using the owning session.
        require_local_printer_request(request, printing_enabled)
        if (request.headers.get("origin") != str(request.base_url).rstrip("/")
                or request.headers.get("x-justbuild-intent") != "print-review"
                or request.query_params):
            raise HTTPException(403, "Use the local owning session and explicit print-review intent")

    @app.post("/api/jobs/{job_id}/print/prepare")
    async def prepare_print_review(job_id: str, request: Request):
        authorize(request, job_id, True)
        print_intent(request)
        await read_local_json(request, set())
        return await run_in_threadpool(print_controller.prepare, job_id)

    @app.post("/api/jobs/{job_id}/print")
    async def print_job(job_id: str, request: Request):
        job = authorize(request, job_id, True)
        if job.get("print_hold"):
            raise HTTPException(409, "This design is held for renewed review; no print was started")
        if job["state"] != "ready_to_print":
            raise HTTPException(409, "A verified, sliced artifact is required before printing")
        if not printing_enabled:
            raise HTTPException(501, "A1 direct LAN adapter is not enabled; no print was started")
        print_intent(request)
        value = await read_local_json(request, {"reviewed_manifest_sha256", "acknowledged_risks", "action"})
        if value["action"] != "start_print":
            raise HTTPException(409, "An explicit start_print action for the reviewed version is required")
        return await run_in_threadpool(print_controller.submit, job_id, value["reviewed_manifest_sha256"],
                                      request.state.owner, value["acknowledged_risks"],
                                      approval={"approved": True, "action": "start_print",
                                                "reviewed_manifest_sha256": value["reviewed_manifest_sha256"]})

    @app.post("/api/jobs/{job_id}/print/monitor")
    async def monitor_print_review(job_id: str, request: Request):
        authorize(request, job_id, True)
        print_intent(request)
        await read_local_json(request, set())
        return await run_in_threadpool(print_controller.monitor, job_id, request.state.owner)

    @app.post("/api/jobs/{job_id}/handoff", status_code=201)
    def desktop_handoff(job_id: str, request: Request):
        authorize(request, job_id, True)
        if not handoff_origin:
            raise HTTPException(503, "Desktop handoff HTTPS origin is not configured")
        try:
            result = handoffs.create(manager, job_id, handoff_origin, owner_sha256=request.state.owner)
        except HandoffError:
            raise HTTPException(409, "A verified, sliced, reviewed design is required for desktop handoff")
        return {key: result[key] for key in ("landing_url", "receiver_uri", "windows_uri", "mac_uri", "expires_at", "sha256", "bytes")}

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    app.mount("/static", StaticFiles(directory=web), name="static")
    app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=4)
    return app

