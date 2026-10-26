"""Handoff capabilities bind bytes, expire and grant neither job access nor print."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

from justbuild.handoff import HandoffError, HandoffStore


class Manager:
    def __init__(self, root, job):
        self.root, self.job = root, job

    def get(self, job_id):
        if job_id != self.job["job_id"]:
            raise FileNotFoundError(job_id)
        return deepcopy(self.job)


@pytest.fixture
def sample(tmp_path):
    job_id = "a" * 32
    root = tmp_path / "jobs"
    folder = root / job_id / "slicing"
    folder.mkdir(parents=True)
    artifact = folder / "enclosure.gcode.3mf"
    # Geometry/slicer validation is owned by prepare_print; use a small payload
    # to exercise exact transfer binding rather than invoking another slicer.
    data = b"an already validated, sliced artifact fixture"
    artifact.write_bytes(data)
    item = {"kind": "sliced_3mf", "path": str(artifact),
            "sha256": hashlib.sha256(data).hexdigest()}
    result = {"job_id": job_id, "status": "ok", "simulation": False,
              "artifacts": [item], "sliced_artifact": deepcopy(item),
              "printer": {"model": "Bambu Lab A1", "id": "private-device-identity"},
              "settings": {"material": "PLA", "nozzle_diameter_mm": 0.4,
                           "bed_type": "Textured PEI Plate", "private": "never shared",
                           "profiles": {"process": {"id": "0.20mm Standard", "path": "/private/profile"}}}}
    job = {"job_id": job_id, "owner_sha256": None, "state": "ready_to_print",
           "label": "PCB prototype", "synthetic": True,
           "stage_results": {"verifying": {"result": {"verdict": "pass"}},
                             "slicing": {"path": "slicing/print_job.json", "result": result}}}
    clock = [1000.0]
    store = HandoffStore(tmp_path / "handoffs", clock=lambda: clock[0])
    return store, Manager(root, job), artifact, data, clock


def create(sample, **kwargs):
    store, manager, *_ = sample
    return store.create(manager, manager.job["job_id"], "https://downloads.example:8443", **kwargs)


def test_public_manifest_and_exact_bytes_exclude_paths_owner_and_printer_identity(sample):
    store, manager, artifact, data, _ = sample
    info = create(sample)
    assert len(bytes.fromhex(info["token"])) == 32
    metadata = store.get(info["token"])
    assert store.payload(info["token"]) == data
    assert store.landing(info["token"]) == metadata
    assert metadata["bytes"] == metadata["size_bytes"] == len(data)
    assert metadata["sha256"] == hashlib.sha256(data).hexdigest()
    assert metadata["permissions"] == ["download_sliced_3mf"]
    assert metadata["grants_print_permission"] is False
    assert metadata["synthetic"] is True
    assert metadata["printer_model"] == "Bambu Lab A1"
    assert metadata["settings"]["profiles"] == {"process": "0.20mm Standard"}
    rendered = json.dumps(metadata)
    for secret in (str(artifact), str(manager.root), "owner_sha256", "token_sha256", "seal",
                   "private-device-identity", "/private/profile", "never shared"):
        assert secret not in rendered
    assert "token" not in metadata
    record = next(store.root.glob("*.json")).read_text()
    assert info["token"] not in record
    assert info["token"] not in next(store.root.glob("*.json")).name


def test_deep_links_are_one_download_and_uri_parameter_is_fully_encoded(sample):
    info = create(sample)
    assert unquote(info["windows_uri"].split("?file=", 1)[1]) == info["download_url"]
    assert unquote(info["receiver_uri"].split("?handoff=", 1)[1]) == info["landing_url"]
    assert info["receiver_uri"].startswith("justbuild://open?handoff=")
    assert info["windows_uri"].count("?") == 1
    assert info["mac_uri"] == "bambustudioopen://" + info["download_url"]
    assert info["manifest_url"] == info["landing_url"] + "/manifest"
    assert info["download_url"] == info["landing_url"] + "/file.gcode.3mf"
    assert urlsplit(info["download_url"]).netloc == "downloads.example:8443"
    assert info["desktop_uri_dispatch_verified"] is False


def test_owner_binding_and_internal_cli_handoff_are_distinct(sample):
    _, manager, *_ = sample
    owner = hashlib.sha256(b"owner session").hexdigest()
    with pytest.raises(HandoffError, match="ownership"):
        create(sample, owner_sha256=owner)
    manager.job["owner_sha256"] = owner
    with pytest.raises(HandoffError, match="ownership"):
        create(sample)
    with pytest.raises(HandoffError, match="ownership"):
        create(sample, owner_sha256="0" * 64)
    assert create(sample, owner_sha256=owner)["permissions"] == ["download_sliced_3mf"]


@pytest.mark.parametrize("change", ["not_ready", "verification_incomplete", "slicing_failed", "simulated",
                                   "simulation_unknown", "manifest_mismatch", "duplicate", "wrong_job", "held"])
def test_unreviewed_or_unverified_outputs_cannot_get_tickets(sample, change):
    _, manager, *_ = sample
    result = manager.job["stage_results"]["slicing"]["result"]
    if change == "not_ready":
        manager.job["state"] = "review_incomplete"
    elif change == "verification_incomplete":
        manager.job["stage_results"]["verifying"]["result"]["verdict"] = "incomplete"
    elif change == "slicing_failed":
        result["status"] = "unsupported"
    elif change == "simulated":
        result["simulation"] = True
    elif change == "simulation_unknown":
        del result["simulation"]
    elif change == "manifest_mismatch":
        result["sliced_artifact"]["sha256"] = "0" * 64
    elif change == "duplicate":
        result["artifacts"].append(deepcopy(result["artifacts"][0]))
    elif change == "wrong_job":
        result["job_id"] = "b" * 32
    elif change == "held":
        manager.job["print_hold"] = {"reason": "Reinspect the real board before any delivery"}
    with pytest.raises(HandoffError):
        create(sample)


@pytest.mark.parametrize("ttl", [0, -1, 3601, 10.5, True, float("inf")])
def test_invalid_lifetimes(sample, ttl):
    with pytest.raises(HandoffError):
        create(sample, ttl_seconds=ttl)


def test_expiry_survives_store_reload_and_rejects_backward_clock(sample):
    store, _, _, _, clock = sample
    info = create(sample, ttl_seconds=30)
    clock[0] = 1029.99
    reloaded = HandoffStore(store.root, clock=lambda: clock[0])
    assert reloaded.get(info["token"])["id"] == info["id"]
    clock[0] = 1030
    for method in (store.get, store.payload, store.landing):
        with pytest.raises(HandoffError):
            method(info["token"])
    clock[0] = 999
    with pytest.raises(HandoffError):
        reloaded.get(info["token"])


@pytest.mark.parametrize("token", ["", "../job.json", "f" * 63, "f" * 65, "F" * 64, "g" * 64, None])
def test_token_path_injection_is_rejected(sample, token):
    with pytest.raises(HandoffError):
        sample[0].payload(token)


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("token_sha256", "0" * 64),
                                        ("expires_epoch", 99999999), ("permissions", ["start_print"]),
                                        ("artifact_relative", "../../private"), ("origin", "https://evil.invalid")])
def test_ticket_fields_cannot_be_edited_without_detection(sample, field, value):
    store, *_ = sample
    info = create(sample)
    path = next(store.root.glob("*.json"))
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(HandoffError):
        store.get(info["token"])


def test_changed_file_rejected_by_manifest_and_payload(sample):
    store, _, artifact, _, _ = sample
    info = create(sample)
    artifact.write_bytes(b"different sliced bytes")
    for method in (store.get, store.payload):
        with pytest.raises(HandoffError):
            method(info["token"])


@pytest.mark.parametrize("kind", ["external_absolute", "traversal", "file_symlink", "directory_symlink"])
def test_outside_paths_and_links_are_rejected(sample, tmp_path, kind):
    store, manager, artifact, data, _ = sample
    result = manager.job["stage_results"]["slicing"]["result"]
    outside = tmp_path / "external.gcode.3mf"
    outside.write_bytes(data)
    if kind == "external_absolute":
        target = str(outside)
    elif kind == "traversal":
        target = str(manager.root / manager.job["job_id"] / ".." / ".." / "external.gcode.3mf")
    elif kind == "file_symlink":
        artifact.unlink()
        artifact.symlink_to(outside)
        target = str(artifact)
    else:
        artifact.unlink()
        artifact.parent.rmdir()
        other = tmp_path / "external-directory"
        other.mkdir()
        (other / artifact.name).write_bytes(data)
        artifact.parent.symlink_to(other, target_is_directory=True)
        target = str(artifact)
    result["artifacts"][0]["path"] = target
    result["sliced_artifact"]["path"] = target
    with pytest.raises(HandoffError):
        create(sample)


def test_post_creation_symlink_replacement_rejected(sample, tmp_path):
    store, _, artifact, data, _ = sample
    info = create(sample)
    outside = tmp_path / "external.gcode.3mf"
    outside.write_bytes(data)
    artifact.unlink()
    artifact.symlink_to(outside)
    with pytest.raises(HandoffError):
        store.payload(info["token"])


def test_file_size_limit_is_applied_on_creation_and_download(sample):
    store, manager, _, data, _ = sample
    limited = HandoffStore(store.root, max_bytes=len(data) - 1, clock=store.clock)
    with pytest.raises(HandoffError):
        limited.create(manager, manager.job["job_id"], "https://downloads.example")
    info = create(sample)
    with pytest.raises(HandoffError):
        limited.payload(info["token"])


@pytest.mark.parametrize("origin", ["http://downloads.example", "https://user:pass@downloads.example",
                                  "https://downloads.example/?x=1", "https://downloads.example/#fragment",
                                  "https://downloads.example/path", "https://downloads.example:0",
                                  "https://downloads.example:99999", "https://downloads.example\\@evil.invalid",
                                  " https://downloads.example", "https://downloads.example\n.evil.invalid",
                                  "file:///private", "https://downloads.example/%0d%0a", "https://bad_host.invalid"])
def test_origin_injection_and_non_https_rejected(sample, origin):
    store, manager, *_ = sample
    with pytest.raises(HandoffError):
        store.create(manager, manager.job["job_id"], origin)


def test_loopback_http_is_an_explicit_debug_option(sample):
    store, manager, *_ = sample
    with pytest.raises(HandoffError):
        store.create(manager, manager.job["job_id"], "http://127.0.0.1:18765")
    debug = HandoffStore(store.root, allow_loopback_http=True, clock=store.clock)
    for origin in ("http://127.0.0.1:18765", "http://localhost:18765", "http://[::1]:18765"):
        assert debug.create(manager, manager.job["job_id"], origin)["download_url"].startswith(origin)
    with pytest.raises(HandoffError):
        debug.create(manager, manager.job["job_id"], "http://192.168.1.5:18765")


def test_concurrent_creation_is_atomic_and_all_tickets_reload(sample):
    store, *_ = sample
    with ThreadPoolExecutor(max_workers=8) as pool:
        infos = list(pool.map(lambda _: create(sample), range(24)))
    assert len({info["token"] for info in infos}) == 24
    assert len(list(store.root.glob("*.json"))) == 24
    assert not list(store.root.glob("*.tmp"))
    assert all(store.get(info["token"])["id"] == info["id"] for info in infos)


def test_core_can_serve_exact_payload_through_http_without_opening_other_files(sample):
    store, _, _, data, clock = sample
    info = create(sample, ttl_seconds=10)
    app = FastAPI()

    @app.get("/handoffs/{token}/file.gcode.3mf")
    def download(token: str):
        try:
            payload = store.payload(token)
        except HandoffError:
            raise HTTPException(404, "Handoff unavailable")
        return Response(payload, media_type="application/octet-stream")

    client = TestClient(app)
    path = urlsplit(info["download_url"]).path
    response = client.get(path)
    assert response.status_code == 200
    assert response.content == data
    assert client.get("/handoffs/" + "0" * 64 + "/file.gcode.3mf").status_code == 404
    assert client.post(path).status_code == 405
    clock[0] = 1010
    assert client.get(path).status_code == 404
