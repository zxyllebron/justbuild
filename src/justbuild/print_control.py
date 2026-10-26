"""Trusted review-action boundary for the opt-in A1 Developer-LAN prototype.

The HTTP caller must authenticate ownership and CSRF before invoking submit.
Preparing never connects; a hold is never cleared here. Public results contain
neither credentials, full printer identity nor server paths.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from .printing import lan
from .printing.common import PrintError, sha256
from .printing.jobs import monitor_print, submit_print
from .printing.slicer import inspect_sliced_3mf

_SHA = re.compile(r"[0-9a-f]{64}")
_JOB = re.compile(r"[0-9a-f]{32}")
_OPTIONS = {"bed_leveling": True, "flow_calibration": False, "vibration_calibration": False,
            "timelapse": False, "use_ams": False, "bed_type": "textured_plate"}
_TTL = 15 * 60


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class ControlError(ValueError):
    def __init__(self, code, message, status="needs_input"):
        super().__init__(message)
        self.code, self.status = code, status


def _error(code, message, status="needs_input"):
    raise ControlError(code, message, status)


def _safe_digest(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _error("INVALID_DIGEST", "A lowercase SHA-256 identity is required.")
    return value


class PrintController:
    def __init__(self, manager, profile_path=None, state_dir=None):
        self.manager = manager
        self.profile_path = Path(profile_path) if profile_path is not None else lan.profile_path()
        self.state_dir = Path(state_dir) if state_dir is not None else Path.home() / ".local/state/justbuild/print-control"
        self.state_dir = self.state_dir.expanduser().absolute()

    def _directory(self):
        path = self.state_dir
        if any(p.is_symlink() for p in (path, *path.parents)):
            _error("PRIVATE_STATE", "Private controller storage cannot traverse symbolic links.")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.stat().st_uid != os.getuid():
            _error("PRIVATE_STATE", "Private controller storage must belong to the current user.")
        path.chmod(0o700)
        return path

    def _write(self, path, value):
        root = self._directory()
        if path.parent != root:
            _error("PRIVATE_STATE", "Invalid private controller destination.")
        temporary = root / (".control-" + secrets.token_hex(16))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_bytes(value)); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _private_read(path, limit=2 * 1024 * 1024):
        if any(p.is_symlink() for p in (path, *path.parents)):
            _error("PRIVATE_STATE", "Invalid private controller storage.")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                _error("PRIVATE_STATE", "Controller records require current-user private permissions.")
            data = stream.read(limit + 1)
        if len(data) > limit:
            _error("PRIVATE_STATE", "Controller record exceeds its size limit.")
        return data

    def _key(self):
        path = self._directory() / "controller.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32)); stream.flush(); os.fsync(stream.fileno())
        value = self._private_read(path, 32)
        if len(value) != 32:
            _error("PRIVATE_STATE", "Invalid controller signing key.")
        return value

    def _sealed_write(self, path, value):
        value = copy.deepcopy(value)
        value["seal"] = hmac.new(self._key(), _bytes(value), hashlib.sha256).hexdigest()
        self._write(path, value)

    def _sealed_read(self, path):
        value = json.loads(self._private_read(path))
        seal = value.pop("seal", None)
        if not isinstance(seal, str) or not hmac.compare_digest(seal, hmac.new(self._key(), _bytes(value), hashlib.sha256).hexdigest()):
            _error("REVIEW_RECORD_CHANGED", "Private review record failed its integrity check.")
        return value

    def _job(self, job_id):
        if not isinstance(job_id, str) or not _JOB.fullmatch(job_id):
            _error("INVALID_JOB", "Invalid job identity.")
        job = self.manager.get(job_id)
        if job.get("job_id") != job_id:
            _error("INVALID_JOB", "Job identity changed.")
        return job

    @staticmethod
    def _owner(job, value):
        _safe_digest(value)
        expected = job.get("owner_sha256")
        if not isinstance(expected, str) or not _SHA.fullmatch(expected) or not hmac.compare_digest(value, expected):
            _error("OWNER_REQUIRED", "An authenticated owning session is required.", "forbidden")

    def _file(self, job_id, value, base=None):
        if not isinstance(value, str) or not value:
            _error("INPUT_PATH", "A local job artifact is required.")
        folder = Path(self.manager.root).resolve() / job_id
        raw = Path(value)
        target = raw if raw.is_absolute() else (base or folder) / raw
        if ".." in target.parts or any(p.is_symlink() for p in (target, *target.parents)):
            _error("INPUT_PATH", "Job artifacts cannot traverse links or parent paths.")
        target = target.resolve()
        if not target.is_relative_to(folder) or not target.is_file() or target.stat().st_size > 128 * 1024 * 1024:
            _error("INPUT_PATH", "An artifact is outside its job or size limit.")
        return target

    def _stage(self, job, stage):
        info = job.get("stage_results", {}).get(stage, {})
        path = self._file(job["job_id"], info.get("path"))
        data = json.loads(path.read_text())
        if data != info.get("result") or data.get("job_id") != job["job_id"] or data.get("schema_version") != "0.1":
            _error("STAGE_CHANGED", "A registered result changed; review must be rebuilt.")
        return data, path

    def _inputs(self, job):
        if job.get("state") != "ready_to_print":
            _error("JOB_NOT_READY", "A verified and sliced review-ready job is required.")
        sliced, sliced_path = self._stage(job, "slicing")
        verify, verify_path = self._stage(job, "verifying")
        cad, cad_path = self._stage(job, "generating")
        request_path = self._file(job["job_id"], "request.json")
        request = json.loads(request_path.read_text())
        if (sliced.get("status") != "ok" or sliced.get("state") != "awaiting_review" or sliced.get("simulation") is not False
                or verify.get("status") != "ok" or verify.get("verdict") != "pass" or verify.get("simulation") is True
                or cad.get("status") != "ok" or cad.get("simulation") is True
                or request.get("job_id") != job["job_id"] or request.get("constraints", {}).get("confirmed") is not True):
            _error("VERIFICATION_REQUIRED", "Only a real slice with passing, current CAD verification can be reviewed.")
        if verify.get("required_not_checked"):
            _error("REQUIRED_CHECK_INCOMPLETE", "A required verification check remains unperformed.")
        if any(c.get("status") not in ("pass", "not_applicable") for c in verify.get("checks", []) if c.get("required") is True):
            _error("REQUIRED_CHECK_INCOMPLETE", "A required verification check is incomplete or failed.")
        bindings = {"request_sha256": sha256(request_path), "cad_result_sha256": sha256(cad_path), "verification_sha256": sha256(verify_path)}
        if any(sliced.get("inputs", {}).get(k) != v for k, v in bindings.items()):
            _error("STALE_SLICE", "The slice is not bound to current design and verification bytes.")
        source = self._file(job["job_id"], request.get("board", {}).get("step_path"), request_path.parent)
        source_sha = sha256(source)
        provenance = cad.get("metadata", {}).get("input_provenance", {})
        if (source_sha != job.get("input_sha256") or source_sha != provenance.get("step_sha256")
                or provenance.get("request_sha256") != bindings["request_sha256"]):
            _error("SOURCE_PROVENANCE", "The original board STEP differs from the verified source version.")
        artifact_bindings = []
        required_cad = {"base_step", "lid_step", "base_stl", "lid_stl", "source_step"}
        carrier_mode = request.get("constraints", {}).get("display", {}).get("support_structure") == "removable_pcb_carrier"
        required_models = {"base_stl", "lid_stl"}
        if carrier_mode:
            required_cad.update({"pcb_carrier_step", "pcb_carrier_stl", "screen_envelope_step"})
            required_models.add("pcb_carrier_stl")
        for entry in cad.get("artifacts", []):
            _safe_digest(entry.get("sha256"))
            file = self._file(job["job_id"], entry.get("path"), cad_path.parent)
            if sha256(file) != entry["sha256"]:
                _error("CAD_ARTIFACT_CHANGED", "A declared CAD or review artifact changed after verification.")
            if entry.get("kind") == "source_step" and entry["sha256"] != source_sha:
                _error("SOURCE_PROVENANCE", "The consumed source snapshot differs from the board STEP.")
            artifact_bindings.append({"kind": entry.get("kind"), "sha256": entry["sha256"]})
        if not required_cad.issubset({a["kind"] for a in artifact_bindings}):
            _error("CAD_ARTIFACT_PROVENANCE", "Authoritative base/lid STEP, STL and consumed source files are required.")
        bindings["source_step_sha256"] = source_sha
        bindings["cad_artifacts_sha256"] = _hash(artifact_bindings)
        if "review_model" in job.get("stage_results", {}):
            review, review_path = self._stage(job, "review_model")
            display_bindings = []
            for entry in review.get("artifacts", []):
                _safe_digest(entry.get("sha256"))
                file = self._file(job["job_id"], entry.get("path"), review_path.parent)
                if sha256(file) != entry["sha256"]:
                    _error("REVIEW_MODEL_CHANGED", "The displayed model differs from the prepared review version.")
                display_bindings.append({"kind": entry.get("kind"), "sha256": entry["sha256"]})
            bindings["review_model_sha256"] = sha256(review_path)
            bindings["review_model_artifacts_sha256"] = _hash(display_bindings)
        for role in ("request", "cad_result"):
            nested = verify.get("inputs", {}).get(role)
            expected = nested.get("sha256") if isinstance(nested, dict) else verify.get("inputs", {}).get(role + "_sha256")
            if expected != bindings[role + "_sha256"]:
                _error("STALE_VERIFICATION", "Verification is not bound to current design bytes.")
        candidates = [a for a in sliced.get("artifacts", []) if a.get("kind") == "sliced_3mf"]
        if len(candidates) != 1 or candidates[0] != sliced.get("sliced_artifact"):
            _error("SLICE_IDENTITY", "Exactly one declared real sliced file is required.")
        ref = candidates[0]; _safe_digest(ref.get("sha256"))
        artifact = self._file(job["job_id"], ref.get("path"), sliced_path.parent)
        if sha256(artifact) != ref["sha256"]:
            _error("SLICE_CHANGED", "Sliced file differs from the reviewed hash.")
        if inspect_sliced_3mf(artifact)["gcode_members"] != ["Metadata/plate_1.gcode"]:
            _error("SINGLE_PLATE_REQUIRED", "The LAN prototype accepts exactly the reviewed first plate.")
        models = sliced.get("inputs", {}).get("models", [])
        if len(models) != len(required_models) or {r.get("kind") for r in models} != required_models:
            _error("MODEL_PROVENANCE", "Every required hash-bound shell and carrier manufacturing model is required.")
        cad_models = {r.get("kind"): r for r in cad.get("artifacts", []) if r.get("kind") in required_models}
        for ref in models:
            original = cad_models.get(ref.get("kind"), {})
            if ref.get("units") != "mm" or original.get("sha256") != ref.get("sha256"):
                _error("MODEL_PROVENANCE", "Slice models differ from the verified CAD artifacts.")
            model = self._file(job["job_id"], ref.get("path"), sliced_path.parent)
            if sha256(model) != ref.get("sha256"):
                _error("MODEL_CHANGED", "A sliced CAD model changed.")
        if sliced.get("printer", {}).get("model") != "Bambu Lab A1" or sliced.get("settings", {}).get("bed_type") != "Textured PEI Plate":
            _error("PROFILE_SCOPE", "Only the reviewed A1 textured PEI profile is supported.")
        return sliced, artifact, verify, {**bindings, "source_print_job_sha256": sha256(sliced_path)}

    @staticmethod
    def _risks(job, verification):
        risks = [
            {"id": "lan_prototype_not_hardware_verified", "message": "Developer-LAN compatibility and physical dispatch have not been accepted on this A1."},
            {"id": "printer_state_and_material_not_observed", "message": "Preparation has not contacted the printer or confirmed its loaded material, nozzle, plate or idle state."},
            {"id": "toolpath_safety_not_verified", "message": "Container integrity does not prove toolpath or physical print safety."},
        ]
        if job.get("synthetic"):
            risks.append({"id": "synthetic_design", "message": "This is a synthetic reference design, not a measured board fit."})
        for i, check in enumerate(verification.get("checks", [])):
            if check.get("status") in ("not_checked", "fail", "incomplete"):
                identity = str(check.get("id", i))
                risks.append({"id": "verification_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                              "message": (str(check.get("category", identity)) + ": " + str(check.get("status")))[:200]})
        return risks

    def _result(self, operation, job_id, error=None, **fields):
        value = {"schema_version": "0.1", "operation": operation, "job_id": job_id if isinstance(job_id, str) and _JOB.fullmatch(job_id) else "unknown",
                 "status": "ok", "issues": [], "retry_allowed": False, "dispatch_verified": False, **fields}
        if error:
            value.update(status=getattr(error, "status", "needs_input"), issues=[{"code": getattr(error, "code", "CONTROL_INPUT"), "message": str(error) if isinstance(error, (ControlError, PrintError)) else "Controller input or private state is unavailable."}])
        return value

    def prepare(self, job_id):
        """Prepare a private review ticket locally. No transport is instantiated."""
        try:
            job = self._job(job_id)
            status = lan.lan_profile_status(path=self.profile_path)
            if not status.get("offline_ready"):
                return self._result("prepare_print_review", job_id, ControlError("PRINTER_CONFIGURATION_REQUIRED", "Complete local A1 pairing and optional LAN runtime setup."),
                                    configuration_required="local_printer_pairing", configuration_url="/printer-setup", network_access_performed=False)
            profile_hash = sha256(self.profile_path)
            profile = lan.load_lan_profile(self.profile_path, expected_sha256=profile_hash)
            sliced, artifact, verification, bindings = self._inputs(job)
            options = lan.validate_lan_print_options(_OPTIONS)
            bound_job = copy.deepcopy(sliced); bound_job["printer"] = {"id": profile["serial"], "model": "Bambu Lab A1"}
            bound_job["sliced_artifact"]["path"] = str(artifact)
            bound_job["artifacts"] = [bound_job["sliced_artifact"]]
            bound_job["lan_print_options"] = options
            created = int(time.time())
            settings = {k: copy.deepcopy(sliced["settings"].get(k)) for k in ("nozzle_diameter_mm", "material", "bed_type")}
            settings["profiles"] = {role: {k: value.get(k) for k in ("id", "sha256")} for role, value in sliced["settings"].get("profiles", {}).items() if role in ("machine", "process", "filament") and isinstance(value, dict)}
            manifest = {"schema_version": "0.1", "job_id": job_id, "review_id": secrets.token_hex(16),
                        "created_at": _iso(created), "expires_at": _iso(created + _TTL), "expires_epoch": created + _TTL,
                        "transport": lan.A1LanTransport.name, "dispatch_verified": False, "simulation": False,
                        "synthetic": job.get("synthetic") is True, "owner_bound": isinstance(job.get("owner_sha256"), str),
                        "print_hold": bool(job.get("print_hold")), "printer": {"model": "Bambu Lab A1", "serial_masked": "***" + profile["serial"][-4:],
                        "id_sha256": hashlib.sha256(profile["serial"].encode()).hexdigest(), "firmware": profile["firmware"], "authorization_mode": profile["authorization_mode"]},
                        "settings": settings, "print_options": options, "sliced_artifact_sha256": sliced["sliced_artifact"]["sha256"],
                        "sliced_artifact_bytes": artifact.stat().st_size, "input_hashes": bindings,
                        "bound_print_job_sha256": _hash(bound_job), "risks": self._risks(job, verification)}
            digest = _hash(manifest)
            ticket = {"manifest": manifest, "manifest_sha256": digest, "owner_sha256": job.get("owner_sha256"),
                      "profile_sha256": profile_hash, "bound_job": bound_job}
            self._sealed_write(self._directory() / ("review-" + digest + ".json"), ticket)
            return self._result("prepare_print_review", job_id, manifest=manifest, manifest_sha256=digest,
                                status="needs_review_hold" if job.get("print_hold") else "awaiting_review", network_access_performed=False)
        except (ControlError, PrintError, OSError, TypeError, ValueError, KeyError) as exc:
            return self._result("prepare_print_review", job_id, exc, network_access_performed=False)

    def _ticket(self, digest, job_id):
        _safe_digest(digest)
        ticket = self._sealed_read(self._directory() / ("review-" + digest + ".json"))
        if ticket.get("manifest_sha256") != digest or _hash(ticket.get("manifest")) != digest or ticket["manifest"].get("job_id") != job_id or _hash(ticket["bound_job"]) != ticket["manifest"].get("bound_print_job_sha256"):
            _error("REVIEW_CHANGED", "The exact prepared review version is required.")
        return ticket

    @staticmethod
    def _receipt(result):
        # Never serialize raw printing results: they contain serials and paths.
        keys = ("status", "state", "simulation", "retry_allowed", "reconciliation_required", "deduplicated", "milestones")
        value = {k: result[k] for k in keys if k in result}
        value["external_side_effects_possible"] = bool(result.get("attempted_at"))
        value["issues"] = [{"code": str(i.get("code", "PRINT_RESULT"))[:100], "message": str(i.get("message", ""))[:300]} for i in result.get("issues", [])]
        return value

    def submit(self, job_id, reviewed_manifest_sha256, owner_sha256, acknowledged_risks, *, approval=None):
        """This call must represent the authenticated browser's explicit start action."""
        try:
            job = self._job(job_id); self._owner(job, owner_sha256)
            ticket = self._ticket(reviewed_manifest_sha256, job_id)
            if ticket.get("owner_sha256") != owner_sha256:
                _error("OWNER_CHANGED", "Review belongs to a different owning session.", "forbidden")
            root = self._directory(); attempt_path = root / ("attempt-" + job_id + ".json")
            lock_fd = os.open(root / ("job-" + job_id + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if attempt_path.exists():
                    previous = self._sealed_read(attempt_path)
                    if previous["manifest_sha256"] != reviewed_manifest_sha256:
                        _error("JOB_ALREADY_ATTEMPTED", "This job already has a dispatch attempt; reconcile without resubmission.")
                    return self._result("submit_print_review", job_id, **{**previous.get("public_receipt", {"status": "needs_input", "state": "unknown", "reconciliation_required": True}), "deduplicated": True})
                if job.get("print_hold"):
                    _error("PRINT_REVIEW_HOLD", "This design remains held for an explicit new-version review.", "needs_review_hold")
                manifest = ticket["manifest"]
                if time.time() >= manifest["expires_epoch"]:
                    _error("REVIEW_EXPIRED", "The print review expired; prepare and review the current version again.")
                if not isinstance(acknowledged_risks, list) or any(not isinstance(i, str) for i in acknowledged_risks) or len(set(acknowledged_risks)) != len(acknowledged_risks) or set(acknowledged_risks) != {r["id"] for r in manifest["risks"]}:
                    _error("RISK_ACKNOWLEDGEMENT", "Acknowledge every risk from this exact review manifest.")
                if approval is not None and (not isinstance(approval, dict) or approval.get("approved") is not True or approval.get("action") != "start_print" or approval.get("reviewed_manifest_sha256") != reviewed_manifest_sha256):
                    _error("EXPLICIT_APPROVAL", "Explicit approval does not match this review version.")
                sliced, artifact, verification, bindings = self._inputs(job)
                if bindings != manifest["input_hashes"] or sliced["sliced_artifact"]["sha256"] != manifest["sliced_artifact_sha256"] or sha256(self.profile_path) != ticket["profile_sha256"]:
                    _error("REVIEW_STALE", "Design, slice or printer pairing changed after review preparation.")
                profile = lan.load_lan_profile(self.profile_path)
                if not hmac.compare_digest(hashlib.sha256(profile["serial"].encode()).hexdigest(), manifest["printer"]["id_sha256"]):
                    _error("PRINTER_CHANGED", "The reviewed printer identity changed.")
                options = lan.validate_lan_print_options(manifest["print_options"])
                if ticket["bound_job"].get("lan_print_options") != options:
                    _error("OPTIONS_CHANGED", "Reviewed printer options changed.")
                transport = lan.A1LanTransport(self.profile_path, state_dir=root / "lan-transport", print_options=options, expected_profile_sha256=ticket["profile_sha256"])
                if not transport.supported:
                    _error("LAN_NOT_CONFIGURED", "The paired Developer-LAN prototype is not configured.")
                operation = root / ("submission-" + secrets.token_hex(16)); operation.mkdir(mode=0o700)
                job_path = operation / "print_job.json"; approval_path = operation / "approval.json"
                self._write_operation(job_path, ticket["bound_job"])
                current = time.time()
                auth = {"schema_version": "0.1", "job_id": job_id, "approved": True, "action": "start_print",
                        "simulation": False, "approval_id": secrets.token_hex(16), "printer_id": profile["serial"],
                        "sliced_artifact_sha256": manifest["sliced_artifact_sha256"], "print_job_sha256": sha256(job_path),
                        "approved_at": _iso(current), "expires_at": _iso(min(current + _TTL, manifest["expires_epoch"])),
                        "reviewed_manifest_sha256": reviewed_manifest_sha256, "acknowledged_risks": acknowledged_risks}
                self._write_operation(approval_path, auth)
                attempt = {"manifest_sha256": reviewed_manifest_sha256, "submission_path": str(operation / "result/submission.json"), "options": options,
                           "public_receipt": {"status": "needs_input", "state": "unknown", "reconciliation_required": True, "external_side_effects_possible": True}}
                self._sealed_write(attempt_path, attempt)
                def validate(candidate, print_job):
                    fresh = self._job(job_id)
                    return (candidate == auth and not fresh.get("print_hold") and fresh.get("owner_sha256") == owner_sha256
                            and time.time() < manifest["expires_epoch"] and sha256(self.profile_path) == ticket["profile_sha256"]
                            and _hash(print_job) == manifest["bound_print_job_sha256"])
                output = submit_print(job_path, approval_path, operation / "result", transport=transport, approval_validator=validate, ledger_dir=root / "submission-ledger")
                raw = json.loads(Path(output).read_text()); attempt["public_receipt"] = self._receipt(raw)
                self._sealed_write(attempt_path, attempt)
                return self._result("submit_print_review", job_id, **attempt["public_receipt"])
            finally:
                os.close(lock_fd)
        except (ControlError, PrintError, OSError, TypeError, ValueError, KeyError) as exc:
            return self._result("submit_print_review", job_id, exc)

    @staticmethod
    def _write_operation(path, value):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_bytes(value)); stream.flush(); os.fsync(stream.fileno())

    def monitor(self, job_id, owner_sha256):
        """Read-only telemetry for the existing attempt. Never retry or start."""
        try:
            job = self._job(job_id); self._owner(job, owner_sha256)
            root = self._directory(); attempt_path = root / ("attempt-" + job_id + ".json")
            attempt = self._sealed_read(attempt_path)
            ticket = self._ticket(attempt["manifest_sha256"], job_id)
            if ticket.get("owner_sha256") != owner_sha256:
                _error("OWNER_CHANGED", "The existing attempt belongs to a different owner.", "forbidden")
            if sha256(self.profile_path) != ticket["profile_sha256"]:
                _error("PRINTER_CHANGED", "Printer pairing changed; reconcile through the original paired configuration.")
            transport = lan.A1LanTransport(self.profile_path, state_dir=root / "lan-transport", print_options=attempt["options"], expected_profile_sha256=ticket["profile_sha256"])
            output_dir = root / ("monitor-" + secrets.token_hex(16)); output_dir.mkdir(mode=0o700)
            path = monitor_print(Path(attempt["submission_path"]), output_dir, transport=transport)
            return self._result("monitor_print_review", job_id, **self._receipt(json.loads(Path(path).read_text())))
        except (ControlError, PrintError, OSError, TypeError, ValueError, KeyError) as exc:
            return self._result("monitor_print_review", job_id, exc)
