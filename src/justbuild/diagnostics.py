"""Portable offline inventory: metadata and filesystem checks, never execution.

An ``ok`` report means the declared core inventory is present. It does not mean
that native CAD imports, a slicer, AI, a network service or hardware was tested.
Only four public slicer-configuration keys are read explicitly; CLI discovery
uses PATH/PATHEXT through shutil.which. No printer/account profile is opened.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys

_PACKAGES = {
    "cadquery": ("cadquery", "core"),
    "ocp": ("cadquery-ocp", "core"),
    "fastapi": ("fastapi", "core"),
    "uvicorn": ("uvicorn", "core"),
    "httpx": ("httpx", "core"),
    "multipart": ("python-multipart", "core"),
    "paho": ("paho-mqtt", "optional_lan"),
}
_TESTED_CAD = {"cadquery": "2.6.1", "ocp": "7.8.1.1.post1"}
_WEB_ROOT = Path(__file__).parent / "web"
_CORE_ASSETS = (
    "index.html", "app.js", "style.css", "dimension-review.css", "dimension-review.js",
    "mounting-review.js", "functional-review.js", "intake-claim.js", "print-review.css", "print-review.js",
    "software-renderer.js", "software-rasterizer.js", "vendor/three.module.js", "vendor/three.core.js",
    "vendor/OrbitControls.js", "vendor/GLTFLoader.js", "vendor/BufferGeometryUtils.js", "vendor/SkeletonUtils.js",
)
_PAIRING_ASSETS = ("printer-setup.html", "printer-setup.js", "printer-setup.css")
_SLICER_ENV = ("JUSTBUILD_BAMBU_BINARY", "JUSTBUILD_BAMBU_SHA256", "JUSTBUILD_BAMBU_VERSION", "JUSTBUILD_BBL_RESOURCES")
_REFERENCE_PROFILES = {
    "machine": "machine/Bambu Lab A1 0.4 nozzle.json",
    "process": "process/0.20mm Standard @BBL A1.json",
    "filament": "filament/Bambu PLA Basic @BBL A1.json",
}
_SAFE_VERSION = re.compile(r"[0-9A-Za-z.!+_-]{1,100}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _package(distribution: str, role: str) -> dict:
    result = {"distribution": distribution, "role": role, "version": None,
              "metadata_installed": False, "import_tested": False}
    try:
        version = metadata.version(distribution)
        if not isinstance(version, str) or _SAFE_VERSION.fullmatch(version) is None:
            result["status"] = "invalid_metadata"
        else:
            result.update(status="ok", version=version, metadata_installed=True)
    except metadata.PackageNotFoundError:
        result["status"] = "missing"
    except Exception:
        # Installed metadata can contain arbitrary exception strings/paths.
        result["status"] = "unavailable_metadata"
    return result


def _nonempty_file(path: Path) -> bool:
    try:
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and info.st_size > 0
    except (OSError, ValueError):
        return False


def _assets(names: tuple[str, ...]) -> dict:
    missing = [name for name in names if not _nonempty_file(_WEB_ROOT / name)]
    return {"status": "needs_input" if missing else "ok", "checked_count": len(names),
            "missing_or_empty": missing, "method": "packaged relative paths; regular nonempty files only",
            "javascript_executed": False, "browser_render_tested": False}


def _binary_file(value: str, expected: str) -> dict:
    result = {"regular_file": False, "executable": False, "sha256_checked": False,
              "sha256_matches": None, "stable_during_read": None}
    if not value:
        return result
    try:
        path = Path(value).expanduser()
        info = path.stat()
        result["regular_file"] = stat.S_ISREG(info.st_mode)
        result["executable"] = result["regular_file"] and os.access(path, os.X_OK)
        if not result["executable"] or _SHA256.fullmatch(expected) is None:
            return result
        # NONBLOCK prevents an accidental FIFO replacement from hanging a doctor.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                result["regular_file"] = False
                return result
            observed = hashlib.file_digest(stream, "sha256").hexdigest()
            after = os.fstat(stream.fileno())
        stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        result.update(sha256_checked=True, stable_during_read=stable,
                      sha256_matches=observed == expected.lower() if stable else None)
    except (OSError, ValueError):
        result["file_check_failed"] = True
    return result


def _slicer() -> dict:
    # No environment enumeration, .env loading, profile discovery or subprocess.
    values = {name: os.environ.get(name, "") for name in _SLICER_ENV}
    configured = {name: bool(value.strip()) for name, value in values.items()}
    binary = _binary_file(values[_SLICER_ENV[0]], values[_SLICER_ENV[1]])
    resources = values[_SLICER_ENV[3]]
    directory = False
    profiles = {role: False for role in _REFERENCE_PROFILES}
    if resources:
        try:
            root = Path(resources).expanduser()
            directory = root.is_dir()
            profiles = {role: _nonempty_file(root / relative) for role, relative in _REFERENCE_PROFILES.items()}
        except (OSError, ValueError):
            pass
    complete = (all(configured.values()) and binary["regular_file"] and binary["executable"]
                and binary["sha256_matches"] is True and directory and all(profiles.values()))
    return {"status": "ok" if complete else "needs_input", "optional": True,
            "configuration_present": configured, "missing_configuration": [name for name, present in configured.items() if not present],
            "sha256_format_valid": _SHA256.fullmatch(values[_SLICER_ENV[1]]) is not None,
            "binary": binary, "resources_directory_present": directory, "a1_reference_profile_files": profiles,
            "configuration_consistent": bool(complete), "program_executed": False,
            "vendor_provenance_verified": False, "declared_version_executed_or_verified": False,
            "profile_inheritance_verified": False, "shared_libraries_or_locale_verified": False,
            "slicing_tested": False}


def doctor_report() -> dict:
    """Return JSON-safe diagnostics without paths, credential reads or actions.

    This function has no runtime dependency beyond Python's standard library.
    Metadata presence does not prove that compiled native extensions can import.
    Slicer hashes attest equality with operator configuration, not vendor origin.
    """
    packages = {name: _package(distribution, role) for name, (distribution, role) in _PACKAGES.items()}
    core_assets = _assets(_CORE_ASSETS)
    python_supported = sys.version_info[:2] >= (3, 11)
    unavailable = [item["distribution"] for item in packages.values()
                   if item["role"] == "core" and item["status"] != "ok"]
    core_ok = python_supported and not unavailable and core_assets["status"] == "ok"
    comparisons = {name: {"tested_version": expected, "observed_version": packages[name]["version"],
                           "matches_tested": packages[name]["version"] == expected}
                   for name, expected in _TESTED_CAD.items()}
    providers = {name: {"installed": bool(shutil.which(name)), "program_executed": False,
                        "login_or_api_access_tested": False, "automatic_enclosure_loop_verified": False}
                 for name in ("codex", "claude")}
    return {"schema_version": "0.1", "operation": "doctor", "status": "ok" if core_ok else "needs_input",
            "status_scope": "offline core metadata and packaged asset inventory only",
            "runtime": {"python_version": platform.python_version(), "python_implementation": platform.python_implementation(),
                        "python_supported": python_supported, "system": platform.system(), "release": platform.release(),
                        "machine": platform.machine()},
            "core": {"status": "ok" if core_ok else "needs_input", "unavailable_distributions": unavailable,
                     "web_assets": core_assets, "native_import_or_cad_tested": False},
            "packages": packages,
            "optional": {"slicer": _slicer(),
                         "lan": {"status": "ok" if packages["paho"]["status"] == "ok" else "needs_input",
                                 "dependency_metadata_present": packages["paho"]["status"] == "ok",
                                 "private_profile_read": False, "connection_tested": False, "dispatch_tested": False},
                         "pairing_assets": _assets(_PAIRING_ASSETS), "model_drivers": providers},
            "tested_environment": {"cad": comparisons,
                                   "tested_cad_versions_match": all(item["matches_tested"] for item in comparisons.values()),
                                   "complete_dependency_lock_verified": False,
                                   "same_geometry_or_print_result_guaranteed": False},
            "check_scope": {"network_access_performed": False, "device_access_performed": False,
                            "private_profile_read": False, "programs_executed": False,
                            "project_dependency_imports_performed": False}}
