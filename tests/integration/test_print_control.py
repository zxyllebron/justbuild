"""Print-controller integration with the real ledger and a non-network adapter."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import socket
import stat
import time
import zipfile

import pytest

from justbuild import print_control as control
from justbuild.printing import lan
from justbuild.printing.common import now
from justbuild.printing.transport import Observation

OWNER = "1" * 64
JOB = "2" * 32


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Manager:
    def __init__(self, root, job):
        self.root, self.job = root, job
    def get(self, job_id):
        if job_id != JOB: raise FileNotFoundError()
        return copy.deepcopy(self.job)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Bans the real network throughout all controller and ledger checks.
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: pytest.fail("No network is authorized in controller tests"))
    values = {"host": "127.0.0.1", "serial": "FAKE-A1-0001", "access_code": "FAKECODE", "firmware": "fixture-01",
              "mqtt_certificate_sha256": "a" * 64, "ftp_certificate_sha256": "b" * 64,
              "authorization_mode": "developer_lan", "developer_mode_confirmed": True}
    profile = tmp_path / "private/a1.json"; lan.save_lan_profile(values, path=profile)
    original_status = lan.lan_profile_status
    def offline_status(*, path):
        result = original_status(path=path)
        # Optional paho installation is independent of core control tests.
        requirements = result.get("ready_prerequisites", {})
        if requirements: requirements["mqtt_dependency_available"] = True
        result["offline_ready"] = bool(requirements) and all(requirements.values())
        return result
    monkeypatch.setattr(lan, "lan_profile_status", offline_status)
    folder = tmp_path / "jobs" / JOB; folder.mkdir(parents=True)
    board_source = folder / "input/board.step"; board_source.parent.mkdir(); board_source.write_bytes(b"immutable source STEP provenance fixture")
    request = {"board": {"step_path": str(board_source)}, "schema_version": "0.1", "job_id": JOB, "constraints": {"confirmed": True}}
    write(folder / "request.json", request)
    cad = {"schema_version": "0.1", "job_id": JOB, "status": "ok", "artifacts": []}
    models = []
    for name in ("base_stl", "lid_stl"):
        model_path = folder / (name + ".stl"); model_path.write_bytes(b"harmless non-CAD provenance fixture")
        models.append({"kind": name, "path": str(model_path), "sha256": digest(model_path), "units": "mm"})
    cad["artifacts"] = copy.deepcopy(models)
    for kind, filename in [("base_step", "base.step"), ("lid_step", "lid.step"), ("source_step", "source.step")]:
        cad_file = folder / filename; cad_file.write_bytes(board_source.read_bytes() if kind == "source_step" else b"immutable case STEP provenance fixture")
        cad["artifacts"].append({"kind": kind, "path": str(cad_file), "sha256": digest(cad_file)})
    cad["metadata"] = {"input_provenance": {"step_sha256": digest(board_source), "request_sha256": digest(folder / "request.json")}}
    write(folder / "cad.json", cad)
    verify = {"schema_version": "0.1", "job_id": JOB, "status": "ok", "verdict": "pass", "checks": [
        {"id": "geometry", "category": "geometry", "required": True, "status": "pass"},
        {"id": "thermal", "category": "thermal", "required": False, "status": "not_checked"}],
        "inputs": {"request_sha256": digest(folder / "request.json"), "cad_result_sha256": digest(folder / "cad.json")}}
    write(folder / "verification.json", verify)
    sliced_file = folder / "fixture.gcode.3mf"
    with zipfile.ZipFile(sliced_file, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; harmless fixture, no physical toolpath\n")
    artifact = {"kind": "sliced_3mf", "path": str(sliced_file), "sha256": digest(sliced_file), "units": "mm"}
    sliced = {"schema_version": "0.1", "job_id": JOB, "status": "ok", "state": "awaiting_review", "simulation": False,
        "printer": {"id": "a1-review-profile-unbound", "model": "Bambu Lab A1"},
        "settings": {"nozzle_diameter_mm": .4, "material": "PLA", "bed_type": "Textured PEI Plate", "profiles": {}},
        "sliced_artifact": artifact, "artifacts": [artifact], "inputs": {
            "request_sha256": digest(folder / "request.json"), "cad_result_sha256": digest(folder / "cad.json"),
            "verification_sha256": digest(folder / "verification.json"), "models": copy.deepcopy(models)}}
    write(folder / "slice.json", sliced)
    job = {"schema_version": "0.1", "job_id": JOB, "owner_sha256": OWNER, "state": "ready_to_print", "synthetic": False, "input_sha256": digest(board_source),
           "stage_results": {name: {"path": filename, "result": data} for name, filename, data in [
               ("generating", "cad.json", cad), ("verifying", "verification.json", verify), ("slicing", "slice.json", sliced)]}}
    manager = Manager(folder.parent, job)
    activity = {"submissions": [], "polls": [], "timeout": False, "constructors": []}
    class OfflineTransport:
        name = "a1_developer_lan_prototype"
        simulation = False
        supported = True
        def __init__(self, profile_path, **kwargs):
            activity["constructors"].append((profile_path, kwargs))
            self.options = lan.validate_lan_print_options(kwargs["print_options"])
        def submit(self, artifact_path, printer_id, idempotency_key):
            activity["submissions"].append((artifact_path.read_bytes(), printer_id, idempotency_key, self.options))
            if activity["timeout"]: raise TimeoutError("FAKECODE must never escape a vendor exception")
            return Observation("start_acknowledged", "fixture-remote", "fixture-device-ack", now())
        def poll(self, remote_job_id, printer_id):
            activity["polls"].append((remote_job_id, printer_id))
            return Observation("printing", remote_job_id, "fixture-correlated-status", now(), 37)
    monkeypatch.setattr(lan, "A1LanTransport", OfflineTransport)
    controller = control.PrintController(manager, profile_path=profile, state_dir=tmp_path / "state")
    def refresh(name):
        item = manager.job["stage_results"][name]; write(folder / item["path"], item["result"])
    return controller, manager, folder, values, profile, activity, refresh


def prepared(setup):
    controller = setup[0]; result = controller.prepare(JOB)
    assert result["status"] == "awaiting_review", result
    return result


def submit(setup, review=None, **kwargs):
    review = review or prepared(setup)
    return setup[0].submit(JOB, review["manifest_sha256"], OWNER, [r["id"] for r in review["manifest"]["risks"]], **kwargs)


def test_prepare_is_local_redacted_and_immutable(setup):
    c, manager, _, values, _, activity, _ = setup
    result = prepared(setup); public = json.dumps(result)
    assert result["network_access_performed"] is False
    assert values["serial"] not in public and values["access_code"] not in public
    assert str(c.state_dir) not in public and str(manager.root) not in public
    assert result["manifest"]["print_options"]["use_ams"] is False
    assert result["manifest"]["dispatch_verified"] is False
    assert activity["constructors"] == []
    path = c.state_dir / ("review-" + result["manifest_sha256"] + ".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(c.state_dir.stat().st_mode) == 0o700
    assert hashlib.sha256(control._bytes(result["manifest"])).hexdigest() == result["manifest_sha256"]
    assert c.prepare(JOB)["manifest_sha256"] != result["manifest_sha256"]


def test_missing_profile_requests_local_configuration(setup):
    c = setup[0]; setup[4].unlink(); result = c.prepare(JOB)
    assert result["status"] == "needs_input"
    assert result["configuration_required"] == "local_printer_pairing"
    assert result["network_access_performed"] is False
    assert setup[5]["submissions"] == []


def test_valid_review_calls_real_core_once_with_exact_identity_options_and_bytes(setup):
    review = prepared(setup); result = submit(setup, review)
    assert result["status"] == "ok" and result["state"] == "start_acknowledged", result
    data, printer, key, options = setup[5]["submissions"][0]
    assert hashlib.sha256(data).hexdigest() == review["manifest"]["sliced_artifact_sha256"]
    assert printer == setup[3]["serial"] and len(key) == 64
    assert options == review["manifest"]["print_options"]
    assert "FAKECODE" not in json.dumps(result) and printer not in json.dumps(result)
    assert "submission_record_path" not in result
    assert setup[1].job.get("print_hold") is None
    assert len(setup[5]["submissions"]) == 1


@pytest.mark.parametrize("owner", [None, "f" * 64, "shared-read-token"])
def test_owner_or_share_is_not_print_authorization(setup, owner):
    review = prepared(setup)
    result = setup[0].submit(JOB, review["manifest_sha256"], owner, [])
    assert result["status"] != "ok" and not setup[5]["submissions"]


def test_unowned_cli_job_cannot_be_claimed_by_controller(setup):
    setup[1].job.pop("owner_sha256")
    result = submit(setup)
    assert result["status"] == "forbidden" and not setup[5]["submissions"]
    assert "owner_sha256" not in setup[1].job


@pytest.mark.parametrize("field,value", [("verdict", "incomplete"), ("status", "failed"), ("simulation", True)])
def test_unverified_or_simulated_never_prepares(setup, field, value):
    setup[1].job["stage_results"]["verifying"]["result"][field] = value; setup[6]("verifying")
    assert setup[0].prepare(JOB)["status"] == "needs_input"
    assert not setup[5]["submissions"]


def test_required_check_failure_cannot_hide_behind_pass_verdict(setup):
    setup[1].job["stage_results"]["verifying"]["result"]["checks"][0]["status"] = "not_checked"; setup[6]("verifying")
    assert setup[0].prepare(JOB)["issues"][0]["code"] == "REQUIRED_CHECK_INCOMPLETE"


def test_hold_is_visible_and_cannot_be_cleared_by_approval(setup):
    setup[1].job["print_hold"] = {"reason": "real_revision_needs_review"}
    result = setup[0].prepare(JOB); assert result["status"] == "needs_review_hold" and result["manifest"]["print_hold"] is True
    outcome = submit(setup, result, approval={"approved": True, "action": "start_print", "reviewed_manifest_sha256": result["manifest_sha256"]})
    assert outcome["status"] == "needs_review_hold" and not setup[5]["submissions"]
    assert setup[1].job["print_hold"] == {"reason": "real_revision_needs_review"}


def test_new_hold_after_prepare_blocks_submission(setup):
    result = prepared(setup); setup[1].job["print_hold"] = True
    assert submit(setup, result)["status"] == "needs_review_hold" and not setup[5]["submissions"]


def test_expiry_is_at_most_15_minutes(setup, monkeypatch):
    result = prepared(setup); manifest = result["manifest"]
    created = control.datetime.fromisoformat(manifest["created_at"]).timestamp()
    assert manifest["expires_epoch"] - created == 900
    monkeypatch.setattr(control.time, "time", lambda: manifest["expires_epoch"])
    assert submit(setup, result)["issues"][0]["code"] == "REVIEW_EXPIRED"
    assert not setup[5]["submissions"]


@pytest.mark.parametrize("what", ["slice", "request", "pairing", "settings", "verification"])
def test_version_changes_block_old_review(setup, what):
    result = prepared(setup)
    if what == "slice": (setup[2] / "fixture.gcode.3mf").write_bytes(b"changed")
    elif what == "request": write(setup[2] / "request.json", {"job_id": JOB, "constraints": {"confirmed": True}, "revision": 2})
    elif what == "pairing":
        setup[3]["firmware"] = "fixture-02"; lan.save_lan_profile(setup[3], path=setup[4])
    elif what == "settings":
        setup[1].job["stage_results"]["slicing"]["result"]["settings"]["material"] = "PETG"; setup[6]("slicing")
    else:
        setup[1].job["stage_results"]["verifying"]["result"]["revision"] = 2; setup[6]("verifying")
    assert submit(setup, result)["status"] != "ok" and not setup[5]["submissions"]


def test_ticket_options_tampering_is_detected(setup):
    result = prepared(setup); c = setup[0]; path = c.state_dir / ("review-" + result["manifest_sha256"] + ".json")
    value = json.loads(path.read_text()); value["manifest"]["print_options"]["timelapse"] = True; path.write_text(json.dumps(value))
    assert submit(setup, result)["issues"][0]["code"] == "REVIEW_RECORD_CHANGED"
    assert not setup[5]["submissions"]


@pytest.mark.parametrize("acknowledged", [[], ["not-a-review-risk"], ["x", "x"]])
def test_all_exact_risks_need_acknowledgement(setup, acknowledged):
    result = prepared(setup)
    outcome = setup[0].submit(JOB, result["manifest_sha256"], OWNER, acknowledged)
    assert outcome["issues"][0]["code"] == "RISK_ACKNOWLEDGEMENT" and not setup[5]["submissions"]


def test_false_optional_explicit_approval_never_prints(setup):
    assert submit(setup, approval={"approved": False})["issues"][0]["code"] == "EXPLICIT_APPROVAL"
    assert not setup[5]["submissions"]


def test_unknown_outcome_is_durable_and_never_retried(setup, monkeypatch):
    result = prepared(setup); setup[5]["timeout"] = True
    first = submit(setup, result)
    assert first["state"] == "unknown" and first["reconciliation_required"] is True
    assert "FAKECODE" not in json.dumps(first)
    monkeypatch.setattr(control.time, "time", lambda: result["manifest"]["expires_epoch"] + 60)
    second = submit(setup, result)
    assert second["deduplicated"] is True and second["state"] == "unknown"
    assert len(setup[5]["submissions"]) == 1


def test_new_manifest_cannot_restart_existing_attempt(setup):
    original = prepared(setup); submit(setup, original)
    another = prepared(setup)
    assert submit(setup, another)["issues"][0]["code"] == "JOB_ALREADY_ATTEMPTED"
    assert len(setup[5]["submissions"]) == 1


def test_monitor_reads_existing_job_without_submitting(setup):
    submit(setup)
    result = setup[0].monitor(JOB, OWNER)
    assert result["state"] == "printing" and len(setup[5]["polls"]) == 1
    assert len(setup[5]["submissions"]) == 1 and "FAKE-A1-0001" not in json.dumps(result)


def test_concurrent_same_review_has_one_dispatch(setup):
    review = prepared(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(setup, review), [1, 2]))
    assert all(r["state"] == "start_acknowledged" for r in results)
    assert sum(r.get("deduplicated", False) for r in results) == 1
    assert len(setup[5]["submissions"]) == 1


def test_external_and_symlink_artifact_are_rejected(setup, tmp_path):
    original = setup[2] / "fixture.gcode.3mf"; external = tmp_path / "outside.gcode.3mf"; external.write_bytes(original.read_bytes())
    original.unlink(); original.symlink_to(external)
    assert setup[0].prepare(JOB)["issues"][0]["code"] == "INPUT_PATH"
    assert not setup[5]["submissions"]


def test_transport_is_pinned_to_exact_reviewed_profile_on_submit_and_monitor(setup):
    result = submit(setup)
    assert result["external_side_effects_possible"] is True
    assert setup[5]["constructors"][-1][1]["expected_profile_sha256"] == digest(setup[4])
    setup[0].monitor(JOB, OWNER)
    assert setup[5]["constructors"][-1][1]["expected_profile_sha256"] == digest(setup[4])


def test_missing_base_or_lid_provenance_blocks_prepare(setup):
    setup[1].job["stage_results"]["slicing"]["result"]["inputs"]["models"].pop(); setup[6]("slicing")
    assert setup[0].prepare(JOB)["issues"][0]["code"] == "MODEL_PROVENANCE"
    assert not setup[5]["submissions"]


def test_required_not_checked_summary_cannot_release(setup):
    setup[1].job["stage_results"]["verifying"]["result"]["required_not_checked"] = ["thermal"]; setup[6]("verifying")
    assert setup[0].prepare(JOB)["issues"][0]["code"] == "REQUIRED_CHECK_INCOMPLETE"


def test_settings_extra_private_fields_do_not_leak_into_public_review(setup):
    setup[1].job["stage_results"]["slicing"]["result"]["settings"]["private_config_path"] = "/private/config/path"
    setup[6]("slicing")
    result = prepared(setup)
    assert "/private/config/path" not in json.dumps(result)


def test_hold_changed_at_core_approval_boundary_still_blocks(setup, monkeypatch):
    review = prepared(setup); original = lan.A1LanTransport
    def constructor(*args, **kwargs):
        adapter = original(*args, **kwargs); setup[1].job["print_hold"] = True; return adapter
    monkeypatch.setattr(lan, "A1LanTransport", constructor)
    outcome = submit(setup, review)
    assert outcome["status"] != "ok" and not setup[5]["submissions"]
    assert outcome["issues"][0]["code"] == "UNTRUSTED_APPROVAL"


@pytest.mark.parametrize("filename", ["input/board.step", "source.step", "base.step", "lid.step", "base_stl.stl", "lid_stl.stl"])
def test_every_authoritative_source_or_cad_mutation_blocks_dispatch(setup, filename):
    review = prepared(setup)
    (setup[2] / filename).write_bytes(b"changed after browser review preparation")
    result = submit(setup, review)
    assert result["status"] != "ok" and not setup[5]["submissions"]
    assert result["issues"][0]["code"] in ("SOURCE_PROVENANCE", "CAD_ARTIFACT_CHANGED")


def test_display_derivative_mutation_blocks_dispatch(setup):
    folder = setup[2]; display = folder / "review.glb"; display.write_bytes(b"display triangle fixture")
    result = {"schema_version": "0.1", "job_id": JOB, "status": "ok", "artifacts": [{"kind": "review_glb_light", "path": str(display), "sha256": digest(display)}]}
    setup[1].job["stage_results"]["review_model"] = {"path": "review.json", "result": result}; write(folder / "review.json", result)
    review = prepared(setup); display.write_bytes(b"changed after review")
    outcome = submit(setup, review)
    assert outcome["issues"][0]["code"] == "REVIEW_MODEL_CHANGED" and not setup[5]["submissions"]


def carrier_case(setup):
    """Only a provenance fixture: no CAD or physical validity is asserted."""
    _,manager,folder,_,_,_,refresh=setup
    request=json.loads((folder/'request.json').read_text())
    request['constraints']['display']={'support_structure':'removable_pcb_carrier'}
    write(folder/'request.json',request)
    cad=manager.job['stage_results']['generating']['result']
    for kind,suffix in [('pcb_carrier_stl','.stl'),('pcb_carrier_step','.step'),('screen_envelope_step','.step')]:
        path=folder/(kind+suffix);path.write_bytes(('nonexecuted '+kind+' provenance fixture').encode())
        cad['artifacts'].append({'kind':kind,'path':str(path),'sha256':digest(path),'units':'mm'})
    cad['metadata']['input_provenance']['request_sha256']=digest(folder/'request.json');refresh('generating')
    verify=manager.job['stage_results']['verifying']['result']
    verify['inputs'].update(request_sha256=digest(folder/'request.json'),cad_result_sha256=digest(folder/'cad.json'));refresh('verifying')
    sliced=manager.job['stage_results']['slicing']['result']
    sliced['inputs'].update(request_sha256=digest(folder/'request.json'),cad_result_sha256=digest(folder/'cad.json'),verification_sha256=digest(folder/'verification.json'))
    sliced['inputs']['models'].append(copy.deepcopy(next(a for a in cad['artifacts'] if a['kind']=='pcb_carrier_stl')))
    refresh('slicing')
    return setup


def test_carrier_review_accepts_all_three_bound_manufacturing_models(setup):
    carrier_case(setup)
    review=prepared(setup)
    assert review['network_access_performed'] is False
    assert len(setup[1].job['stage_results']['slicing']['result']['inputs']['models'])==3
    assert not setup[5]['submissions']


def test_carrier_review_rejects_omitted_third_model(setup):
    carrier_case(setup)
    setup[1].job['stage_results']['slicing']['result']['inputs']['models'].pop()
    setup[6]('slicing')
    result=setup[0].prepare(JOB)
    assert result['issues'][0]['code']=='MODEL_PROVENANCE'
    assert not setup[5]['submissions'] and not setup[5]['constructors']


@pytest.mark.parametrize('kind',['pcb_carrier_step','pcb_carrier_stl','screen_envelope_step'])
def test_carrier_review_requires_every_authoritative_added_part(setup,kind):
    carrier_case(setup)
    cad=setup[1].job['stage_results']['generating']['result']
    cad['artifacts']=[a for a in cad['artifacts'] if a['kind']!=kind];setup[6]('generating')
    # Rebind local evidence so the missing-role check is tested directly.
    verify=setup[1].job['stage_results']['verifying']['result'];verify['inputs']['cad_result_sha256']=digest(setup[2]/'cad.json');setup[6]('verifying')
    sliced=setup[1].job['stage_results']['slicing']['result'];sliced['inputs'].update(cad_result_sha256=digest(setup[2]/'cad.json'),verification_sha256=digest(setup[2]/'verification.json'));setup[6]('slicing')
    result=setup[0].prepare(JOB)
    assert result['issues'][0]['code']=='CAD_ARTIFACT_PROVENANCE'
    assert not setup[5]['submissions']


def test_carrier_part_change_cannot_reuse_a_previous_review(setup):
    carrier_case(setup);review=prepared(setup)
    (setup[2]/'pcb_carrier_stl.stl').write_bytes(b'changed third-part geometry')
    result=submit(setup,review)
    assert result['status']!='ok' and not setup[5]['submissions']
