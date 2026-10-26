"""Approval, durable at-most-one dispatch attempts, and read-only monitoring."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .common import (OutputOverlapError, PrintError, checked_file, digest, envelope, now, positive_number, protect_output,
                     read_json, record_error, require_envelope, sha256, text_field, write_json)
from .slicer import inspect_sliced_3mf
from .transport import Observation, STATES, Transport, UnsupportedCloudTransport


# The main chat supplies a trusted authenticated review-action verifier. The JSON
# approval document is never itself accepted as proof of a human action for live use.
ApprovalValidator = Callable[[dict, dict], bool]


def _time(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
        return stamp.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PrintError("INVALID_TIMESTAMP", "A timezone-aware ISO timestamp is required.") from exc


def _approval(approval: dict, job: dict, job_path: Path, validator: ApprovalValidator | None) -> None:
    require_envelope(approval, job["job_id"])
    if approval.get("approved") is not True or approval.get("action") != "start_print":
        raise PrintError("APPROVAL_REQUIRED", "An explicit start-print review action is required.", "needs_input")
    if (approval.get("printer_id") != job["printer"]["id"]
            or digest(approval.get("sliced_artifact_sha256")) != job["sliced_artifact"]["sha256"]
            or digest(approval.get("print_job_sha256")) != sha256(job_path)):
        raise PrintError("APPROVAL_MISMATCH", "Approval must match the exact print job, printer and sliced artifact.")
    text_field(approval.get("approval_id"), "approval_id")
    current = datetime.now(timezone.utc)
    approved, expires = _time(approval.get("approved_at")), _time(approval.get("expires_at"))
    if approved > current or expires <= current or expires <= approved or expires - approved > timedelta(minutes=15):
        raise PrintError("APPROVAL_EXPIRED", "Approval must be current and valid for at most 15 minutes.", "needs_input")
    if job["simulation"]:
        if approval.get("simulation") is not True:
            raise PrintError("SIMULATION_APPROVAL", "Simulation approval must explicitly set simulation=true.")
    else:
        if approval.get("simulation") is True:
            raise PrintError("SIMULATION_APPROVAL", "Simulation approval cannot authorize live dispatch.")
        if validator is None:
            raise PrintError("REVIEW_AUTH_NOT_INTEGRATED", "Main must integrate an authenticated review-action validator before live dispatch.", "unsupported")
        try:
            accepted = validator(approval, job)
        except Exception as exc:
            raise PrintError("UNTRUSTED_APPROVAL", "Authenticated review authorization could not be verified.") from exc
        if accepted is not True:
            raise PrintError("UNTRUSTED_APPROVAL", "Authenticated review authorization was rejected.")


def _observation(value: Observation, *, remote_job_id: str | None = None) -> dict:
    if not isinstance(value, Observation) or value.state not in STATES:
        raise PrintError("INVALID_OBSERVATION", "Transport returned an unknown job observation.")
    text_field(value.remote_job_id, "remote_job_id")
    text_field(value.evidence_id, "evidence_id")
    if remote_job_id is not None and value.remote_job_id != remote_job_id:
        raise PrintError("REMOTE_JOB_MISMATCH", "Telemetry belongs to another remote print job.")
    stamp = _time(value.observed_at)
    current = datetime.now(timezone.utc)
    if stamp > current + timedelta(seconds=5) or stamp < current - timedelta(minutes=5):
        raise PrintError("STALE_TELEMETRY", "Telemetry must be fresh and have a bounded clock skew.")
    progress = value.progress_percent
    if progress is not None:
        if progress != 0:
            progress = positive_number(progress, "progress_percent")
        if isinstance(progress, bool) or progress > 100:
            raise PrintError("INVALID_PROGRESS", "Progress must be between 0 and 100.")
    result = {"state": value.state, "remote_job_id": value.remote_job_id,
              "evidence_id": value.evidence_id, "observed_at": value.observed_at}
    if progress is not None:
        result["progress_percent"] = progress
    return result


def _apply(record: dict, observation: dict) -> None:
    old = record.get("observation")
    previous_known_state = record.get("last_known_state", old["state"] if old else None)
    if old:
        if _time(observation["observed_at"]) < _time(old["observed_at"]):
            raise PrintError("STALE_TELEMETRY", "Telemetry is older than the last observation.")
        if previous_known_state in {"completed", "failed", "cancelled"} and observation["state"] != previous_known_state:
            raise PrintError("TERMINAL_STATE", "A terminal remote job cannot change to another state.")
        ranks = {"uploaded": 0, "printer_received": 1, "start_acknowledged": 2, "printing": 3, "paused": 3}
        if previous_known_state in ranks and observation["state"] in ranks and ranks[observation["state"]] < ranks[previous_known_state]:
            raise PrintError("STATE_REGRESSION", "A later observation cannot regress to an earlier dispatch stage.")
    record["state"] = observation["state"]
    record["remote_job_id"] = observation["remote_job_id"]
    record["observation"] = observation
    if observation["state"] != "unknown":
        record["last_known_state"] = observation["state"]
    # An observed later phase does not prove that earlier callbacks were observed.
    milestone = {"uploaded": "upload", "printer_received": "printer_receipt",
                 "start_acknowledged": "start_acknowledgement", "completed": "reported_completion"}.get(observation["state"])
    if milestone:
        record["milestones"][milestone] = {"status": "observed", "evidence_id": observation["evidence_id"],
                                            "observed_at": observation["observed_at"]}


def submit_print(print_job_path: Path, approval_path: Path, output_dir: Path, *,
                 transport: Transport | None = None, approval_validator: ApprovalValidator | None = None,
                 ledger_dir: Path | None = None) -> Path:
    """Submit once. Unknown outcomes require reconciliation, never an automatic retry.

    Default live transport is unsupported. Tests inject a simulation explicitly.
    For multiple workers, ledger_dir MUST be the same persistent directory.
    """
    output_dir = Path(output_dir).resolve()
    print_job_path, approval_path = Path(print_job_path), Path(approval_path)
    protect_output(output_dir, "submission.json", [print_job_path, approval_path])
    result = envelope()
    result.update({"operation": "submit_print", "state": "blocked", "simulation": False, "retry_allowed": False})
    transport = transport or UnsupportedCloudTransport()
    try:
        job_hash = sha256(print_job_path)
        job, approval = read_json(print_job_path), read_json(approval_path)
        if sha256(print_job_path) != job_hash:
            raise PrintError("INPUT_CHANGED", "Print job changed while reading it.")
        result["job_id"] = text_field(job.get("job_id"), "job_id")
        require_envelope(job, result["job_id"])
        if job.get("status") != "ok" or job.get("state") != "awaiting_review" or type(job.get("simulation")) is not bool:
            raise PrintError("PRINT_JOB_NOT_READY", "Only a review-ready prepared job may be submitted.", "needs_input")
        result["simulation"] = job["simulation"]
        printer = job.get("printer", {})
        text_field(printer.get("id"), "printer.id")
        if printer.get("model") != "Bambu Lab A1":
            raise PrintError("PRINTER_MODEL", "Only Bambu Lab A1 is supported.", "unsupported")
        ref = job.get("sliced_artifact")
        artifact_path = checked_file(ref, print_job_path)
        protect_output(output_dir, "submission.json", [artifact_path])
        expected_kind = "simulation_slice" if job["simulation"] else "sliced_3mf"
        if ref.get("kind") != expected_kind:
            raise PrintError("ARTIFACT_KIND", "Slice kind and simulation mode disagree.")
        if not job["simulation"]:
            inspect_sliced_3mf(artifact_path)
        if type(transport.simulation) is not bool or transport.simulation != job["simulation"]:
            raise PrintError("TRANSPORT_MODE", "Simulation and live transports cannot be mixed.")
        _approval(approval, job, print_job_path, approval_validator)
        if sha256(print_job_path) != job_hash:
            raise PrintError("INPUT_CHANGED", "Print job changed while validating approval.")
        if not transport.supported:
            raise PrintError("CLOUD_DISPATCH_UNVERIFIED", getattr(transport, "reason", "Transport unavailable."), "unsupported")
        ledger = (Path(ledger_dir) if ledger_dir is not None else output_dir / "submission-ledger").resolve()
        ledger.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(json.dumps([job["job_id"], printer["id"], job["simulation"]], separators=(",", ":")).encode()).hexdigest()
        record_path = ledger / f"{key}.json"
        with (ledger / f"{key}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if record_path.exists():
                previous = read_json(record_path)
                if previous.get("sliced_artifact_sha256") != ref["sha256"]:
                    raise PrintError("JOB_ALREADY_ATTEMPTED", "This job/printer already has a dispatch attempt for different bytes.")
                result.update(previous)
                result["deduplicated"] = True
                result["reconciliation_required"] = previous.get("state") in {"unknown", "dispatching"}
                if result["reconciliation_required"]:
                    result["status"] = "needs_input"
            else:
                record = {**result, "transport": transport.name, "printer_id": printer["id"],
                          "sliced_artifact_sha256": ref["sha256"], "print_job_sha256": job_hash,
                          "idempotency_key": key, "state": "dispatching", "attempted_at": now(),
                          "milestones": {name: {"status": "not_observed"} for name in
                                         ("upload", "printer_receipt", "start_acknowledgement", "reported_completion", "physical_completion")}}
                # Persist BEFORE any external side effect; a crash cannot trigger a blind retry.
                write_json(record_path, record)
                try:
                    # Hash once more at the dispatch boundary. Transport must send these bytes.
                    if sha256(artifact_path) != ref["sha256"]:
                        raise PrintError("INPUT_CHANGED", "Slice changed at the dispatch boundary.")
                    # Give the adapter a private snapshot of the approved bytes,
                    # independent of later edits to the review download artifact.
                    payload_dir = ledger / (key + "-payload")
                    payload_dir.mkdir(exist_ok=False)
                    payload = payload_dir / ("simulation.json" if job["simulation"] else "slice.gcode.3mf")
                    shutil.copyfile(artifact_path, payload)
                    if sha256(payload) != ref["sha256"]:
                        raise PrintError("INPUT_CHANGED", "Slice changed while snapshotting dispatch bytes.")
                    payload.chmod(0o400)
                    observed = _observation(transport.submit(payload, printer["id"], key))
                    _apply(record, observed)
                    if observed["state"] == "unknown":
                        record.update({"status": "needs_input", "reconciliation_required": True})
                except Exception:
                    # Do not leak exception payloads: vendor exceptions may contain tokens/URLs.
                    record.update({"status": "needs_input", "state": "unknown", "reconciliation_required": True})
                    record["issues"].append({"code": "SUBMISSION_OUTCOME_UNKNOWN", "severity": "warning",
                                              "message": "Dispatch outcome is unknown. Reconcile the existing attempt; do not resubmit.",
                                              "evidence": {}})
                write_json(record_path, record)
                result.update(record)
        result["submission_record_path"] = str(record_path)
    except OutputOverlapError:
        raise
    except PrintError as exc:
        record_error(result, exc)
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        record_error(result, PrintError("INVALID_SUBMISSION_INPUT", "Malformed approval/job input or inaccessible state storage."))
    return write_json(output_dir / "submission.json", result)


def monitor_print(submission_path: Path, output_dir: Path, *, transport: Transport | None = None) -> Path:
    """Read-only remote reconciliation. Polling never dispatches or retries a job.

    A submit result can refer to its authoritative submission_record_path.
    All writers lock the authoritative record's sibling lock.
    """
    output_dir = Path(output_dir).resolve()
    submission_path = Path(submission_path).resolve()
    protect_output(output_dir, "print_status.json", [submission_path])
    result = envelope()
    result.update({"operation": "monitor_print", "state": "unknown", "retry_allowed": False})
    transport = transport or UnsupportedCloudTransport()
    try:
        initial = read_json(submission_path)
        if initial.get("submission_record_path"):
            submission_path = Path(initial["submission_record_path"]).resolve()
        protect_output(output_dir, "print_status.json", [submission_path])
        with submission_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record = read_json(submission_path)
            result["job_id"] = text_field(record.get("job_id"), "job_id")
            require_envelope(record, result["job_id"])
            result["simulation"] = record.get("simulation", False)
            if (transport.name != record.get("transport") or transport.simulation != record.get("simulation")
                    or not transport.supported):
                raise PrintError("MONITOR_TRANSPORT", "Use the same supported transport and mode as the submission.", "unsupported")
            remote_id = record.get("remote_job_id")
            result.update(record)
            result["operation"] = "monitor_print"
            if not remote_id:
                raise PrintError("RECONCILIATION_REQUIRED", "No remote job ID was received. Reconcile via the transport's idempotency lookup or operator evidence.", "needs_input")
            text_field(record.get("printer_id"), "printer_id")
            try:
                observed = _observation(transport.poll(remote_id, record["printer_id"]), remote_job_id=remote_id)
                _apply(record, observed)
                record["status"] = "needs_input" if observed["state"] == "unknown" else "ok"
                record["reconciliation_required"] = observed["state"] == "unknown"
                record["issues"] = [issue for issue in record.get("issues", []) if issue.get("code") != "SUBMISSION_OUTCOME_UNKNOWN"]
                write_json(submission_path, record)
                result.update(record)
                result["operation"] = "monitor_print"
            except PrintError:
                raise
            except Exception:
                result.update(record)
                result["operation"] = "monitor_print"
                raise PrintError("MONITOR_UNAVAILABLE", "Polling failed; the last recorded state remains unchanged.", "needs_input")
    except OutputOverlapError:
        raise
    except PrintError as exc:
        record_error(result, exc)
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        record_error(result, PrintError("INVALID_MONITOR_INPUT", "Malformed submission record or inaccessible state storage."))
    result["submission_record_path"] = str(submission_path)
    return write_json(output_dir / "print_status.json", result)
