"""Offline diagnostic contract: mocked metadata and temporary non-executed files."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from justbuild import diagnostics as doctor

VERSIONS = {"cadquery": "2.6.1", "cadquery-ocp": "7.8.1.1.post1", "fastapi": "0.141.1",
            "uvicorn": "0.52.3", "httpx": "0.28.1", "python-multipart": "0.0.32", "paho-mqtt": "2.1.0"}
SECRET = "PRIVATE_SENTINEL_MUST_NOT_APPEAR"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    web = tmp_path / "package-web"
    for name in (*doctor._CORE_ASSETS, *doctor._PAIRING_ASSETS):
        path = web / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"nonempty static fixture; not executed")
    versions = dict(VERSIONS)
    queried = []
    def version(name):
        queried.append(name)
        if name not in versions:
            raise metadata.PackageNotFoundError(name)
        value = versions[name]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(doctor.metadata, "version", version)
    monkeypatch.setattr(doctor, "_WEB_ROOT", web)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor, "sys", SimpleNamespace(version_info=(3, 12, 3)))
    for key in doctor._SLICER_ENV:
        monkeypatch.delenv(key, raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("Diagnostics must not execute a program or use a network socket")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)

    binary = tmp_path / "private-user-home" / "official-bambu"
    binary.parent.mkdir()
    binary.write_bytes(b"fixture binary bytes that must never execute")
    binary.chmod(0o700)
    resource_root = tmp_path / "private-resource-directory"
    for relative in doctor._REFERENCE_PROFILES.values():
        path = resource_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")

    def configure():
        for key, value in zip(doctor._SLICER_ENV, (str(binary), hashlib.sha256(binary.read_bytes()).hexdigest(), SECRET, str(resource_root))):
            monkeypatch.setenv(key, value)
    return SimpleNamespace(web=web, versions=versions, queried=queried, binary=binary,
                           resources=resource_root, configure=configure, root=tmp_path)


def test_missing_optional_features_do_not_fail_core(rig):
    rig.versions.pop("paho-mqtt")
    report = doctor.doctor_report()
    assert report["status"] == report["core"]["status"] == "ok"
    assert report["optional"]["slicer"]["status"] == "needs_input"
    assert report["optional"]["lan"]["status"] == "needs_input"
    assert not report["optional"]["model_drivers"]["codex"]["installed"]
    assert set(rig.queried) == set(VERSIONS)
    assert report["core"]["unavailable_distributions"] == []
    assert report["tested_environment"]["tested_cad_versions_match"] is True
    assert report["tested_environment"]["same_geometry_or_print_result_guaranteed"] is False
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("missing", ["cadquery", "cadquery-ocp", "fastapi", "uvicorn", "httpx", "python-multipart"])
def test_missing_core_dependency_is_needs_input(rig, missing):
    rig.versions.pop(missing)
    report = doctor.doctor_report()
    assert report["status"] == report["core"]["status"] == "needs_input"
    assert missing in report["core"]["unavailable_distributions"]
    assert all(value["import_tested"] is False for value in report["packages"].values())


def test_old_python_version_marks_core_unavailable(rig, monkeypatch):
    monkeypatch.setattr(doctor, "sys", SimpleNamespace(version_info=(3, 10, 9)))
    report = doctor.doctor_report()
    assert report["runtime"]["python_supported"] is False
    assert report["status"] == "needs_input"


def test_version_difference_is_comparison_not_reproduction_guarantee(rig):
    rig.versions.update(cadquery="2.7.0")
    rig.versions["cadquery-ocp"] = "7.9.0"
    report = doctor.doctor_report()
    assert report["status"] == "ok"  # Inventory presence, explicitly not execution/compatibility.
    tested = report["tested_environment"]
    assert tested["tested_cad_versions_match"] is False
    assert tested["cad"]["cadquery"] == {"tested_version": "2.6.1", "observed_version": "2.7.0", "matches_tested": False}
    assert tested["complete_dependency_lock_verified"] is False
    assert tested["same_geometry_or_print_result_guaranteed"] is False


@pytest.mark.parametrize("bad", [None, "/home/private/" + SECRET, RuntimeError("private metadata: " + SECRET)])
def test_corrupt_metadata_is_redacted_and_core_incomplete(rig, bad):
    rig.versions["cadquery"] = bad
    report = doctor.doctor_report()
    assert report["status"] == "needs_input"
    assert report["packages"]["cadquery"]["version"] is None
    assert SECRET not in json.dumps(report)


def test_core_asset_missing_or_empty_is_reported_with_relative_name(rig):
    (rig.web / "vendor" / "three.core.js").unlink()
    (rig.web / "app.js").write_bytes(b"")
    report = doctor.doctor_report()
    assert report["status"] == "needs_input"
    assert set(report["core"]["web_assets"]["missing_or_empty"]) == {"app.js", "vendor/three.core.js"}
    assert str(rig.root) not in json.dumps(report)


def test_pairing_page_is_optional_for_core_cad_and_review(rig):
    (rig.web / "printer-setup.js").unlink()
    report = doctor.doctor_report()
    assert report["status"] == "ok"
    assert report["optional"]["pairing_assets"]["status"] == "needs_input"
    assert report["optional"]["pairing_assets"]["missing_or_empty"] == ["printer-setup.js"]


def test_complete_slicer_configuration_hashes_without_executing_or_echoing_values(rig):
    rig.configure()
    report = doctor.doctor_report()
    slicer = report["optional"]["slicer"]
    assert slicer["status"] == "ok" and slicer["configuration_consistent"] is True
    assert slicer["binary"] == {"regular_file": True, "executable": True, "sha256_checked": True,
                                "sha256_matches": True, "stable_during_read": True}
    assert all(slicer["a1_reference_profile_files"].values())
    assert slicer["vendor_provenance_verified"] is False
    assert slicer["declared_version_executed_or_verified"] is False
    assert slicer["profile_inheritance_verified"] is False
    assert slicer["shared_libraries_or_locale_verified"] is False
    assert slicer["program_executed"] is slicer["slicing_tested"] is False
    serialized = json.dumps(report, allow_nan=False)
    assert SECRET not in serialized and str(rig.root) not in serialized
    assert str(rig.binary) not in serialized and str(rig.resources) not in serialized


def test_hash_mismatch_is_optional_needs_input(rig, monkeypatch):
    rig.configure(); monkeypatch.setenv("JUSTBUILD_BAMBU_SHA256", "0" * 64)
    report = doctor.doctor_report()
    assert report["status"] == "ok"
    assert report["optional"]["slicer"]["status"] == "needs_input"
    assert report["optional"]["slicer"]["binary"]["sha256_matches"] is False


@pytest.mark.parametrize("digest", ["bad", "", "0" * 63, "g" * 64, SECRET])
def test_invalid_hash_is_not_used_or_returned(rig, monkeypatch, digest):
    rig.configure(); monkeypatch.setenv("JUSTBUILD_BAMBU_SHA256", digest)
    report = doctor.doctor_report()
    slicer = report["optional"]["slicer"]
    assert slicer["status"] == "needs_input"
    assert slicer["sha256_format_valid"] is False
    assert slicer["binary"]["sha256_checked"] is False
    assert SECRET not in json.dumps(report)


def test_empty_resources_directory_is_not_complete_configuration(rig):
    rig.configure()
    for relative in doctor._REFERENCE_PROFILES.values():
        (rig.resources / relative).unlink()
    report = doctor.doctor_report()
    slicer = report["optional"]["slicer"]
    assert slicer["resources_directory_present"] is True
    assert not any(slicer["a1_reference_profile_files"].values())
    assert slicer["status"] == "needs_input"


def test_nonexecutable_file_is_not_read(rig, monkeypatch):
    rig.configure(); rig.binary.chmod(0o600)
    opened = []
    real_open = doctor.os.open
    def watch(*args, **kwargs):
        opened.append(args[0]); return real_open(*args, **kwargs)
    monkeypatch.setattr(doctor.os, "open", watch)
    report = doctor.doctor_report()
    assert report["optional"]["slicer"]["binary"]["regular_file"] is True
    assert report["optional"]["slicer"]["binary"]["executable"] is False
    assert report["optional"]["slicer"]["binary"]["sha256_checked"] is False
    assert opened == []


def test_directory_cannot_be_hashed_as_binary(rig, monkeypatch):
    rig.configure(); monkeypatch.setenv("JUSTBUILD_BAMBU_BINARY", str(rig.resources))
    report = doctor.doctor_report()
    assert report["optional"]["slicer"]["binary"]["regular_file"] is False
    assert report["optional"]["slicer"]["binary"]["sha256_checked"] is False
    assert report["optional"]["slicer"]["status"] == "needs_input"


def test_binary_io_errors_are_redacted(rig, monkeypatch):
    rig.configure()
    def blocked(*args, **kwargs):
        raise PermissionError(str(rig.binary) + SECRET)
    monkeypatch.setattr(doctor.os, "open", blocked)
    report = doctor.doctor_report()
    assert report["optional"]["slicer"]["binary"]["file_check_failed"] is True
    assert report["optional"]["slicer"]["status"] == "needs_input"
    assert SECRET not in json.dumps(report) and str(rig.root) not in json.dumps(report)


def test_provider_detection_does_not_mean_login_or_ai_loop(rig, monkeypatch):
    requested = []
    def which(name):
        requested.append(name)
        return "/home/private/" + SECRET + "/" + name
    monkeypatch.setattr(doctor.shutil, "which", which)
    report = doctor.doctor_report()
    assert requested == ["codex", "claude"]
    for provider in report["optional"]["model_drivers"].values():
        assert provider["installed"] is True
        assert provider["program_executed"] is provider["login_or_api_access_tested"] is provider["automatic_enclosure_loop_verified"] is False
    assert SECRET not in json.dumps(report)


def test_reads_only_whitelisted_environment_and_configured_binary(rig, monkeypatch):
    rig.configure()
    values = {name: os.environ[name] for name in doctor._SLICER_ENV}
    class Environment(dict):
        def get(self, name, default=""):
            assert name in doctor._SLICER_ENV, "Private or unrelated environment accessed"
            return super().get(name, default)
        def __iter__(self):
            pytest.fail("Do not enumerate environment secrets")
        def items(self):
            pytest.fail("Do not enumerate environment secrets")
    real_os = doctor.os
    reads = []
    def open_binary(path, *args, **kwargs):
        assert Path(path) == rig.binary, "Do not read any printer/private profile"
        reads.append(Path(path)); return real_os.open(path, *args, **kwargs)
    monkeypatch.setattr(doctor, "os", SimpleNamespace(environ=Environment(values), access=real_os.access,
        open=open_binary, fdopen=real_os.fdopen, fstat=real_os.fstat, O_RDONLY=real_os.O_RDONLY, O_NONBLOCK=real_os.O_NONBLOCK, X_OK=real_os.X_OK))
    report = doctor.doctor_report()
    assert reads == [rig.binary]
    assert report["check_scope"] == {"network_access_performed": False, "device_access_performed": False,
                                    "private_profile_read": False, "programs_executed": False,
                                    "project_dependency_imports_performed": False}


def test_changed_file_never_reports_hash_match(rig, monkeypatch):
    rig.configure()
    real_os = doctor.os; reads = []
    def changing(descriptor):
        info = real_os.fstat(descriptor); reads.append(1)
        values = {name: getattr(info, name) for name in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
        if len(reads) == 2:
            values["st_mtime_ns"] += 1
        return SimpleNamespace(**values)
    monkeypatch.setattr(doctor, "os", SimpleNamespace(environ=real_os.environ, access=real_os.access,
        open=real_os.open, fdopen=real_os.fdopen, fstat=changing, O_RDONLY=real_os.O_RDONLY, O_NONBLOCK=real_os.O_NONBLOCK, X_OK=real_os.X_OK))
    report = doctor.doctor_report()
    binary = report["optional"]["slicer"]["binary"]
    assert binary["sha256_checked"] is True and binary["stable_during_read"] is False
    assert binary["sha256_matches"] is None
    assert report["optional"]["slicer"]["status"] == "needs_input"
