"""One real synthetic build; every other case reuses it or blocks before CAD."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess

import pytest

from justbuild import selfcheck


def forbidden(*_args, **_kwargs):
    pytest.fail("Offline self-test attempted a network/provider/slicer subprocess")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    pytest.importorskip("cadquery")
    output = tmp_path_factory.mktemp("selfcheck-real") / "run"
    from justbuild import slicing_config
    from justbuild.printing.slicer import BambuCliSlicer
    with pytest.MonkeyPatch.context() as patch:
        # Even malformed complete optional configuration must remain irrelevant.
        for key, value in {"JUSTBUILD_BAMBU_BINARY": "/never/run/Studio", "JUSTBUILD_BAMBU_SHA256": "malformed-env-hash",
                           "JUSTBUILD_BAMBU_VERSION": "untrusted-env", "JUSTBUILD_BBL_RESOURCES": "/never/read/profiles"}.items():
            patch.setenv(key, value)
        patch.setattr(slicing_config, "SlicingConfig", forbidden)
        patch.setattr(BambuCliSlicer, "slice", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(socket.socket, "connect", forbidden)
        environment = dict(os.environ)
        result = selfcheck.run_self_check(output)
        assert dict(os.environ) == environment
    return result


def test_real_standard_sample_and_independent_checks(sample):
    assert sample["status"] == "ok" and sample["verdict"] == "pass", sample
    assert sample["sample"]["pcb_dimensions_mm"] == [80, 55, 1.6]
    assert sample["sample"]["hole_centers_xy_mm"] == [[5, 5], [75, 5], [5, 50], [75, 50]]
    assert sample["independent_verification"]["verdict"] == "pass"
    assert sample["independent_verification"]["required_check_count"] > 0
    assert all(c["status"] in ("pass", "not_applicable") for c in sample["checks"])
    pcb = sample["geometry"]["pcb"]
    assert pcb["dimensions_mm"] == pytest.approx([80, 55, 1.6], abs=1e-6)
    assert pcb["volume_mm3"] == pytest.approx(7040 - 4 * math.pi * 1.5**2 * 1.6, abs=1e-6)
    assert sample["geometry"]["board_assembly"]["solid_count"] == 4
    for part in ("pcb", "base", "lid"):
        assert sample["geometry"][part]["valid"] is True
        assert sample["geometry"][part]["solid_count"] == 1
        assert sample["geometry"][part]["volume_mm3"] > 0


def test_four_measured_real_step_axes_are_zero(sample):
    assert len(sample["mounting_holes"]) == 4
    for hole, expected in zip(sample["mounting_holes"], [[5, 5, .8], [75, 5, .8], [5, 50, .8], [75, 50, .8]]):
        for part, diameter in [("pcb", 3.), ("base", 2.)]:
            assert hole[part]["center_mm"] == pytest.approx(expected, abs=1e-9)
            assert hole[part]["axis"] == pytest.approx([0, 0, 1], abs=1e-9)
            assert hole[part]["diameter_mm"] == pytest.approx(diameter, abs=1e-9)
            assert hole[part]["axis_offset_mm"] == pytest.approx(0, abs=1e-9)


def test_report_artifacts_and_explicit_unperformed_scope(sample):
    assert json.loads(Path(sample["report_path"]).read_text()) == sample
    assert Path(sample["job_path"]).is_file()
    for kind in ("base_step", "lid_step", "base_stl", "lid_stl", "review_glb"):
        assert Path(sample["artifact_paths"][kind]).is_file()
    assert sample["execution"]["cad"] == "run"
    assert sample["execution"]["independent_verification"] == "run"
    for operation in ("slicing", "model_provider", "network", "printing"):
        assert sample["execution"][operation] == "not_run"
    assert any(c["category"] == "thermal" and c["status"] == "not_checked" for c in sample["not_checked_scope"])
    assert "physical fit" in sample["scope_notice"]


def test_semantic_fingerprint_uses_quantities_not_export_bytes_or_paths(sample):
    semantic = sample["semantic_geometry"]
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == sample["semantic_geometry_sha256"]
    assert sample["output_dir"] not in encoded.decode() and sample["job_id"] not in encoded.decode()
    assert semantic["parts"]["pcb"]["bounds_min"] == [0, 0, 0]
    assert semantic["parts"]["pcb"]["bounds_max"] == [8000000, 5500000, 160000]
    assert semantic["parts"]["pcb"]["volume"] == 69947611
    assert all(h[p]["axis_offset"] == 0 for h in semantic["mounting_holes"] for p in ("pcb", "base"))


@pytest.mark.parametrize("change", ["failed_verdict", "incomplete_verdict", "required_not_checked", "invalid_solid", "cad_failure"])
def test_failed_or_incomplete_pipeline_cannot_pass(sample, tmp_path, monkeypatch, change):
    original = json.loads(Path(sample["job_path"]).read_text())
    def pipeline(manager, *, wait):
        assert wait is True and manager.slicing.slicer is None
        job = copy.deepcopy(original)
        folder = manager.root / job["job_id"]; folder.mkdir()
        verification = job["stage_results"]["verifying"]["result"]
        if change == "failed_verdict": verification.update(status="failed", verdict="fail")
        elif change == "incomplete_verdict": verification.update(status="needs_input", verdict="incomplete"); job["state"] = "review_incomplete"
        elif change == "required_not_checked": next(c for c in verification["checks"] if c["required"])["status"] = "not_checked"
        elif change == "invalid_solid": next(c for c in verification["checks"] if c["id"] == "solid:base")["status"] = "fail"
        else: job["stage_results"]["generating"]["result"]["status"] = "failed"
        return job
    monkeypatch.setattr(selfcheck, "create_demo", pipeline)
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = selfcheck.run_self_check(tmp_path / "run")
    assert result["verdict"] != "pass" and result["status"] != "ok"
    assert any(c["status"] == "fail" for c in result["checks"])
    assert Path(result["report_path"]).is_file()


def test_nonempty_destination_is_untouched(tmp_path):
    output = tmp_path / "already-used"; output.mkdir(); sentinel = output / "valuable.bin"; sentinel.write_bytes(b"retain this file")
    before = sentinel.read_bytes(), sentinel.stat().st_mtime_ns
    with pytest.raises(ValueError, match="empty or new"):
        selfcheck.run_self_check(output)
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns) == before
    assert sorted(p.name for p in output.iterdir()) == ["valuable.bin"]


def test_existing_file_or_symlink_output_is_rejected(tmp_path):
    file = tmp_path / "file"; file.write_text("retain")
    with pytest.raises(ValueError): selfcheck.run_self_check(file)
    outside = tmp_path / "outside"; outside.mkdir(); link = tmp_path / "link"; link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        selfcheck.run_self_check(link)
    assert file.read_text() == "retain" and not list(outside.iterdir())


def test_missing_geometry_backend_is_explicit_needs_input(tmp_path, monkeypatch):
    original = selfcheck.importlib.import_module
    def unavailable(name, *args, **kwargs):
        if name == "cadquery": raise ModuleNotFoundError("fixture backend absent")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(selfcheck.importlib, "import_module", unavailable)
    monkeypatch.setattr(selfcheck, "create_demo", forbidden)
    existing_empty = tmp_path / "empty"; existing_empty.mkdir()
    result = selfcheck.run_self_check(existing_empty)
    assert result["status"] == "needs_input" and result["verdict"] == "incomplete"
    assert result["issues"][0]["code"] == "GEOMETRY_BACKEND_UNAVAILABLE"
    assert not (existing_empty / "jobs").exists()
    assert json.loads((existing_empty / "report.json").read_text()) == result
