from __future__ import annotations

import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from justbuild.printing import (FakeSlicer, FakeTransport, Observation, cloud_capabilities,
                               monitor_print, prepare_print, submit_print)
from justbuild.printing.common import PrintError, now, read_json, sha256, write_json
from justbuild.printing.slicer import BambuCliSlicer, inspect_sliced_3mf


def prepare(fixture_job, slicer=FakeSlicer()):
    f = fixture_job
    return prepare_print(f["request"], f["cad"], f["verification"], f["root"] / "prepared", slicer=slicer)


def approve(job_path: Path) -> Path:
    job = read_json(job_path)
    current = datetime.now(timezone.utc)
    return write_json(job_path.parent / "approval.json", {
        "schema_version": "0.1", "job_id": job["job_id"], "approved": True, "action": "start_print",
        "simulation": job["simulation"], "printer_id": job["printer"]["id"], "approval_id": "test-human-action",
        "print_job_sha256": sha256(job_path), "sliced_artifact_sha256": job["sliced_artifact"]["sha256"],
        "approved_at": current.isoformat(), "expires_at": (current + timedelta(minutes=5)).isoformat(),
    })


def change(path, **updates):
    data = read_json(path)
    data.update(updates)
    write_json(path, data)


def issue(result):
    return result["issues"][-1]["code"]


def test_prepare_simulation_is_unprintable_and_labelled(fixture_job):
    result = read_json(prepare(fixture_job))
    assert result["status"] == "ok" and result["state"] == "awaiting_review"
    assert result["simulation"] is True
    assert result["sliced_artifact"]["kind"] == "simulation_slice"
    assert "SIMULATION-not-printable" in result["sliced_artifact"]["path"]


@pytest.mark.parametrize("verdict", ["incomplete", "fail", None])
def test_incomplete_verification_cannot_slice(fixture_job, verdict):
    change(fixture_job["verification"], verdict=verdict)
    result = read_json(prepare(fixture_job))
    assert result["status"] == "needs_input" and issue(result) == "VERIFICATION_NOT_PASSED"
    assert result["artifacts"] == []


def test_missing_slicer_is_unsupported(fixture_job):
    assert read_json(prepare(fixture_job, None))["status"] == "unsupported"


@pytest.mark.parametrize("target", ["request", "cad"])
def test_stale_verification_is_rejected(fixture_job, target):
    path = fixture_job[target]
    path.write_text(path.read_text() + "\n")
    assert issue(read_json(prepare(fixture_job))) == "STALE_VERIFICATION"


def test_missing_provenance_is_not_silently_accepted(fixture_job):
    change(fixture_job["verification"], inputs={})
    result = read_json(prepare(fixture_job))
    assert result["status"] == "needs_input" and issue(result) == "VERIFICATION_PROVENANCE"


def test_mesh_hash_mismatch_is_rejected(fixture_job):
    (fixture_job["root"] / "base.stl").write_text("changed")
    assert issue(read_json(prepare(fixture_job))) == "HASH_MISMATCH"


@pytest.mark.parametrize("field,value,code", [
    ("material", "PETG", "MATERIAL_MISMATCH"),
    ("nozzle_diameter_mm", 0.6, "NOZZLE_MISMATCH"),
    ("nozzle_diameter_mm", True, "INVALID_NUMBER"),
])
def test_profile_choice_must_match_content(fixture_job, field, value, code):
    request = read_json(fixture_job["request"])
    request["printing"][field] = value
    write_json(fixture_job["request"], request)
    fixture_job["rebind"]()
    assert issue(read_json(prepare(fixture_job))) == code


def test_partial_inherited_profile_is_rejected(fixture_job):
    profile = fixture_job["root"] / "machine.json"
    change(profile, inherits="fdm_machine_common")
    request = read_json(fixture_job["request"])
    request["printing"]["profiles"]["machine"]["sha256"] = sha256(profile)
    write_json(fixture_job["request"], request)
    fixture_job["rebind"]()
    assert issue(read_json(prepare(fixture_job))) == "PROFILE_INCOMPLETE"


@pytest.mark.parametrize("field,value", [("job_id", "other"), ("printer_id", "other"),
                                         ("sliced_artifact_sha256", "0" * 64), ("print_job_sha256", "0" * 64),
                                         ("approved", False), ("simulation", False)])
def test_approval_binding_rejections_never_dispatch(fixture_job, field, value):
    job = prepare(fixture_job)
    approval = approve(job)
    change(approval, **{field: value})
    transport = FakeTransport()
    result = read_json(submit_print(job, approval, fixture_job["root"] / "submit", transport=transport))
    assert result["status"] != "ok" and transport.submissions == 0


def test_expired_approval_never_dispatches(fixture_job):
    job = prepare(fixture_job)
    approval = approve(job)
    change(approval, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    transport = FakeTransport()
    result = read_json(submit_print(job, approval, fixture_job["root"] / "submit", transport=transport))
    assert issue(result) == "APPROVAL_EXPIRED" and transport.submissions == 0


def test_slice_changed_after_review_never_dispatches(fixture_job):
    job = prepare(fixture_job)
    approval = approve(job)
    Path(read_json(job)["sliced_artifact"]["path"]).write_text("changed")
    transport = FakeTransport()
    result = read_json(submit_print(job, approval, fixture_job["root"] / "submit", transport=transport))
    assert issue(result) == "HASH_MISMATCH" and transport.submissions == 0


def test_ambiguous_timeout_is_durable_and_not_retried(fixture_job):
    job = prepare(fixture_job)
    approval = approve(job)
    transport = FakeTransport(timeout_on_submit=True)
    out = fixture_job["root"] / "submit"
    first = read_json(submit_print(job, approval, out, transport=transport))
    second = read_json(submit_print(job, approval, out, transport=transport))
    assert first["state"] == second["state"] == "unknown"
    assert first["retry_allowed"] is False and second["deduplicated"] is True
    assert transport.submissions == 1
    status = read_json(monitor_print(Path(first["submission_record_path"]), out, transport=transport))
    assert issue(status) == "RECONCILIATION_REQUIRED" and transport.submissions == 1


def test_crash_after_attempt_record_cannot_resubmit(fixture_job):
    class CrashingTransport(FakeTransport):
        def submit(self, *args):
            self.submissions += 1
            raise KeyboardInterrupt("simulated process exit")
    job = prepare(fixture_job)
    approval = approve(job)
    transport = CrashingTransport()
    out = fixture_job["root"] / "submit"
    with pytest.raises(KeyboardInterrupt):
        submit_print(job, approval, out, transport=transport)
    result = read_json(submit_print(job, approval, out, transport=transport))
    assert result["state"] == "dispatching" and result["reconciliation_required"] is True
    assert transport.submissions == 1


def test_two_workers_sharing_ledger_dispatch_only_once(fixture_job):
    job = prepare(fixture_job)
    approval = approve(job)
    transport = FakeTransport()
    ledger = fixture_job["root"] / "shared-ledger"
    def run(index):
        return read_json(submit_print(job, approval, fixture_job["root"] / str(index),
                                     transport=transport, ledger_dir=ledger))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert transport.submissions == 1
    assert sum(result.get("deduplicated", False) for result in results) == 1


def test_upload_does_not_prove_receipt_start_or_completion(fixture_job):
    job = prepare(fixture_job)
    transport = FakeTransport()
    result = read_json(submit_print(job, approve(job), fixture_job["root"] / "submit", transport=transport))
    assert result["state"] == "uploaded" and result["simulation"] is True
    assert result["milestones"]["upload"]["status"] == "observed"
    assert all(result["milestones"][name]["status"] == "not_observed"
               for name in ("printer_receipt", "start_acknowledgement", "reported_completion", "physical_completion"))


def test_completion_telemetry_is_not_physical_acceptance(fixture_job):
    job = prepare(fixture_job)
    transport = FakeTransport()
    result = read_json(submit_print(job, approve(job), fixture_job["root"] / "submit", transport=transport))
    transport.next_state = "completed"
    status = read_json(monitor_print(Path(result["submission_record_path"]), fixture_job["root"] / "status", transport=transport))
    assert status["state"] == "completed"
    assert status["milestones"]["reported_completion"]["status"] == "observed"
    assert status["milestones"]["physical_completion"]["status"] == "not_observed"
    assert transport.submissions == 1


@pytest.mark.parametrize("variant,code", [("remote", "REMOTE_JOB_MISMATCH"), ("stale", "STALE_TELEMETRY"),
                                         ("future", "STALE_TELEMETRY"), ("progress", "INVALID_PROGRESS")])
def test_bad_telemetry_does_not_change_ledger(fixture_job, variant, code):
    job = prepare(fixture_job)
    class BadTelemetry(FakeTransport):
        def poll(self, remote_job_id, printer_id):
            observation = super().poll(remote_job_id, printer_id)
            if variant == "remote": return replace(observation, remote_job_id="other")
            if variant == "stale": return replace(observation, observed_at="2000-01-01T00:00:00Z")
            if variant == "future": return replace(observation, observed_at="2099-01-01T00:00:00Z")
            return replace(observation, progress_percent=101)
    transport = BadTelemetry()
    result = read_json(submit_print(job, approve(job), fixture_job["root"] / "submit", transport=transport))
    record = Path(result["submission_record_path"])
    before = record.read_bytes()
    status = read_json(monitor_print(record, fixture_job["root"] / "status", transport=transport))
    assert issue(status) == code and record.read_bytes() == before


def test_vendor_exception_text_is_not_exported(fixture_job):
    job = prepare(fixture_job)
    class SecretFailure(FakeTransport):
        def submit(self, *args):
            raise RuntimeError("SECRET_EXCEPTION_MUST_NOT_BE_PERSISTED")
    result_path = submit_print(job, approve(job), fixture_job["root"] / "submit", transport=SecretFailure())
    assert "SECRET_EXCEPTION_MUST_NOT_BE_PERSISTED" not in result_path.read_text()
    assert read_json(result_path)["state"] == "unknown"


def test_plain_live_approval_has_no_authority(fixture_job):
    job = prepare(fixture_job)
    data = read_json(job)
    path = fixture_job["root"] / "sliced.gcode.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; fixture only, never send\n")
    data["simulation"] = False
    data["sliced_artifact"] = {"kind": "sliced_3mf", "path": str(path), "sha256": sha256(path)}
    write_json(job, data)
    result = read_json(submit_print(job, approve(job), fixture_job["root"] / "submit"))
    assert result["status"] == "unsupported" and issue(result) == "REVIEW_AUTH_NOT_INTEGRATED"


def test_unsliced_3mf_is_rejected(tmp_path):
    path = tmp_path / "unsliced.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", "fixture")
    with pytest.raises(PrintError, match="no nonempty"):
        inspect_sliced_3mf(path)


def test_slicer_hash_pin_rejects_replaced_binary(tmp_path):
    path = tmp_path / "slicer"
    path.write_text("not an executable")
    slicer = BambuCliSlicer(path, executable_sha256="0" * 64, version="02.08.02.61")
    with pytest.raises(PrintError) as error:
        slicer.slice([], {}, {}, tmp_path / "out")
    assert error.value.code == "SLICER_HASH_MISMATCH"


def test_cloud_probe_does_not_claim_live_capability():
    result = cloud_capabilities()
    assert result["status"] == "unsupported" and result["network_access_performed"] is False
    assert set(result["capabilities"].values()) == {"not_checked"}


def test_result_path_cannot_overwrite_an_input(fixture_job):
    protected = fixture_job["root"] / "print_job.json"
    protected.write_bytes(fixture_job["request"].read_bytes())
    before = protected.read_bytes()
    with pytest.raises(ValueError, match="overlaps"):
        prepare_print(protected, fixture_job["cad"], fixture_job["verification"], protected.parent, slicer=FakeSlicer())
    assert protected.read_bytes() == before


def test_monitor_accepts_result_and_updates_authoritative_ledger(fixture_job):
    job = prepare(fixture_job)
    transport = FakeTransport()
    submitted = submit_print(job, approve(job), fixture_job["root"] / "submit", transport=transport)
    transport.next_state = "printing"
    status = read_json(monitor_print(submitted, fixture_job["root"] / "status", transport=transport))
    assert status["state"] == "printing"
    assert read_json(Path(status["submission_record_path"]))["state"] == "printing"


def test_state_cannot_regress_through_unknown(fixture_job):
    job = prepare(fixture_job)
    transport = FakeTransport(initial_state="printing")
    result = submit_print(job, approve(job), fixture_job["root"] / "submit", transport=transport)
    transport.next_state = "unknown"
    first = read_json(monitor_print(result, fixture_job["root"] / "status", transport=transport))
    assert first["state"] == "unknown" and first["last_known_state"] == "printing"
    transport.next_state = "uploaded"
    second = read_json(monitor_print(result, fixture_job["root"] / "status", transport=transport))
    assert issue(second) == "STATE_REGRESSION"


def test_terminal_job_cannot_resume(fixture_job):
    job = prepare(fixture_job)
    transport = FakeTransport(initial_state="completed")
    result = submit_print(job, approve(job), fixture_job["root"] / "submit", transport=transport)
    transport.next_state = "printing"
    status = read_json(monitor_print(result, fixture_job["root"] / "status", transport=transport))
    assert issue(status) == "TERMINAL_STATE"


def test_simulated_verification_cannot_release_live_output(fixture_job):
    from justbuild.printing import SliceOutput
    class MislabelledSlicer:
        def slice(self, models, profiles, settings, output_dir):
            output_dir.mkdir(parents=True)
            path = output_dir / "fake.3mf"
            path.write_text("not printable")
            return SliceOutput(path, False, {})
    result = read_json(prepare(fixture_job, MislabelledSlicer()))
    assert issue(result) == "SIMULATED_VERIFICATION" and result["status"] == "needs_input"
