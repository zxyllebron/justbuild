"""Local synthetic CAD/independent-verification smoke test; never slices or prints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import threading
import time

from .contracts import digest, read_json, write_json
from .demo import create_demo
from .orchestrator import JobManager

_LINEAR_QUANTUM = 1e-5
_VOLUME_QUANTUM = 1e-4
_AXIS_ZERO_TOLERANCE = 1e-9
_EXPECTED_HOLES = [(5., 5.), (75., 5.), (5., 50.), (75., 50.)]


class _NoSlicing:
    """Instance-local, deliberately independent of every process environment key."""
    slicer = None
    resources = None

    def synthetic_settings(self, _job_dir):
        return None


class _OfflineManager(JobManager):
    def __init__(self, root):
        # Reuse JobManager's actual create/confirm/run implementation. Its normal
        # constructor reads optional server slicer configuration, so this private
        # bootstrap creates only this instance's local resources, without touching
        # the environment, services or any module/global configuration.
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="justbuild-selfcheck")
        self.lock = threading.RLock()
        self.slicing = _NoSlicing()


def _reserve(output_dir):
    path = Path(output_dir).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Self-test output cannot traverse symbolic links")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Self-test output must be an empty or new directory")
    path.mkdir(parents=True, exist_ok=True)
    lock = path / ".selfcheck-reserved"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise ValueError("Self-test output is already in use") from None
    os.close(descriptor)
    # Close the check/reserve race without deleting any pre-existing contents.
    if any(p != lock for p in path.iterdir()):
        lock.unlink()
        raise ValueError("Self-test output changed while being reserved")
    return path, lock


def _check(report, identity, passed, measurements=None):
    report["checks"].append({"id": identity, "status": "pass" if passed else "fail", "required": True,
                             "measurements": measurements or {}})


def _artifact(cad, cad_path, kind):
    references = [a for a in cad.get("artifacts", []) if a.get("kind") == kind]
    if len(references) != 1:
        raise ValueError("Expected exactly one " + kind + " artifact")
    ref = references[0]; raw = Path(ref["path"])
    path = raw if raw.is_absolute() else cad_path.parent / raw
    if not path.is_file() or digest(path) != ref.get("sha256"):
        raise ValueError("Artifact is missing or changed: " + kind)
    return path.resolve()


def _shape_record(shape, kernel):
    valid = kernel.validity(shape, 1e-7)
    box = shape.BoundingBox()
    low, high = [box.xmin, box.ymin, box.zmin], [box.xmax, box.ymax, box.zmax]
    return {**valid, "bounds_min_mm": low, "bounds_max_mm": high,
            "dimensions_mm": [b-a for a, b in zip(low, high)], "volume_mm3": shape.Volume()}


def _bore(shape, center, diameter, kernel):
    candidates = [c for c in kernel.bore_candidates(shape, center, [0, 0, 1], .001)
                  if c["full_circular_face"] and abs(c["diameter_mm"] - diameter) < 1e-6
                  and c["axis_angle_deg"] <= 1e-6 and c["axis_offset_mm"] <= .001]
    if not candidates:
        raise ValueError("The actual STEP does not contain the expected full circular bore")
    value = min(candidates, key=lambda c: c["axis_offset_mm"])
    direction = value["axis_direction"]
    direction = direction if direction[2] >= 0 else [-v for v in direction]
    location = value["axis_point_mm"]
    along = sum((p-a)*d for p, a, d in zip(center, location, direction))
    return {"center_mm": [a + along*d for a, d in zip(location, direction)], "axis": direction,
            "diameter_mm": value["diameter_mm"], "axis_offset_mm": value["axis_offset_mm"],
            "axis_angle_deg": value["axis_angle_deg"]}


def _quantized(record):
    q = lambda value, quantum: int(round(value / quantum))
    return {"valid": record["valid"], "solid_count": record["solid_count"],
            "bounds_min": [q(v, _LINEAR_QUANTUM) for v in record["bounds_min_mm"]],
            "bounds_max": [q(v, _LINEAR_QUANTUM) for v in record["bounds_max_mm"]],
            "volume": q(record["volume_mm3"], _VOLUME_QUANTUM),
            "solid_volumes": sorted(q(v, _VOLUME_QUANTUM) for v in record["solid_volumes_mm3"])}


def _assess(report, job, directory):
    stages = job.get("stage_results", {})
    cad = stages.get("generating", {}).get("result", {})
    verification = stages.get("verifying", {}).get("result", {})
    required = [c for c in verification.get("checks", []) if c.get("required")]
    _check(report, "synthetic_fixture", job.get("synthetic") is True)
    _check(report, "cad_generation", cad.get("status") == "ok", {"status": cad.get("status")})
    _check(report, "independent_verification", verification.get("status") == "ok" and verification.get("verdict") == "pass",
           {"status": verification.get("status"), "verdict": verification.get("verdict")})
    _check(report, "all_required_checks", bool(required) and all(c.get("status") in ("pass", "not_applicable") for c in required)
           and not verification.get("required_not_checked"), {"count": len(required)})
    solids = [c for c in verification.get("checks", []) if c.get("id") in ("solid:board", "solid:base", "solid:lid")]
    _check(report, "independent_valid_solids", len(solids) == 3 and all(c.get("status") == "pass" for c in solids))
    datums = [c for c in verification.get("checks", []) if c.get("category") == "mounting_holes" and c.get("id", "").endswith(":board_datum")]
    _check(report, "four_independent_pcb_datums", len(datums) == 4 and all(c.get("status") == "pass" for c in datums))
    report["independent_verification"] = {"verdict": verification.get("verdict"), "status": verification.get("status"),
                                           "required_check_count": len(required), "check_summary": verification.get("check_summary", {})}
    report["not_checked_scope"] = [{k: c.get(k) for k in ("id", "category", "required", "status", "method")}
                                   for c in verification.get("checks", []) if c.get("status") == "not_checked"]
    report["pipeline_state"] = job.get("state")
    if any(c["status"] == "fail" for c in report["checks"]):
        return
    cad_path = directory / stages["generating"]["path"]
    verify_path = directory / stages["verifying"]["path"]
    if read_json(cad_path) != cad or read_json(verify_path) != verification:
        raise ValueError("Registered reports changed after pipeline execution")
    request = read_json(directory / "request.json")
    from .verification import geometry as kernel
    board_path = Path(request["board"]["step_path"])
    if digest(board_path) != job.get("input_sha256"):
        raise ValueError("Synthetic input STEP changed after generation")
    board = kernel.load_step(board_path)
    selected = request["board"]["metadata"]["pcb_solid_index"]
    pcb = board.Solids()[selected]
    base = kernel.load_step(_artifact(cad, cad_path, "base_step"))
    lid = kernel.load_step(_artifact(cad, cad_path, "lid_step"))
    geometry = {name: _shape_record(shape, kernel) for name, shape in (("board_assembly", board), ("pcb", pcb), ("base", base), ("lid", lid))}
    report["geometry"] = geometry
    _check(report, "fresh_step_validity", all(r["valid"] for r in geometry.values())
           and geometry["board_assembly"]["solid_count"] == 4 and all(geometry[k]["solid_count"] == 1 for k in ("pcb", "base", "lid")))
    _check(report, "standard_pcb_dimensions", all(abs(a-b) <= 1e-6 for a, b in zip(geometry["pcb"]["dimensions_mm"], (80, 55, 1.6))),
           {"expected_mm": [80, 55, 1.6], "actual_mm": geometry["pcb"]["dimensions_mm"]})
    expected_volume = 80 * 55 * 1.6 - 4 * math.pi * 1.5**2 * 1.6
    _check(report, "standard_pcb_volume", abs(geometry["pcb"]["volume_mm3"] - expected_volume) <= 1e-6,
           {"expected_mm3": expected_volume, "actual_mm3": geometry["pcb"]["volume_mm3"]})
    holes = []
    for x, y in _EXPECTED_HOLES:
        center = [x, y, .8]
        holes.append({"expected_center_mm": center, "pcb": _bore(pcb, center, 3., kernel), "base": _bore(base, center, 2., kernel)})
    report["mounting_holes"] = holes
    _check(report, "four_measured_coaxial_holes", len(holes) == 4 and all(h[p]["axis_offset_mm"] <= _AXIS_ZERO_TOLERANCE and h[p]["axis_angle_deg"] <= 1e-6 for h in holes for p in ("pcb", "base")),
           {"axis_zero_tolerance_mm": _AXIS_ZERO_TOLERANCE, "pcb_offsets_mm": [h["pcb"]["axis_offset_mm"] for h in holes], "base_offsets_mm": [h["base"]["axis_offset_mm"] for h in holes]})
    semantic = {"schema": "justbuild-synthetic-geometry-v1", "linear_quantum_mm": _LINEAR_QUANTUM, "volume_quantum_mm3": _VOLUME_QUANTUM,
                "parts": {name: _quantized(value) for name, value in geometry.items()},
                "mounting_holes": [{name: {"center": [int(round(v/_LINEAR_QUANTUM)) for v in h[name]["center_mm"]],
                    "axis": [int(round(v/1e-6)) for v in h[name]["axis"]], "diameter": int(round(h[name]["diameter_mm"]/_LINEAR_QUANTUM)),
                    "axis_offset": int(round(h[name]["axis_offset_mm"]/_LINEAR_QUANTUM))} for name in ("pcb", "base")} for h in holes]}
    report["semantic_geometry"] = semantic
    report["semantic_geometry_sha256"] = hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    report["artifact_paths"] = {a["kind"]: str((cad_path.parent / a["path"]).resolve()) if not Path(a["path"]).is_absolute() else a["path"] for a in cad.get("artifacts", [])}


def run_self_check(output_dir: Path) -> dict:
    """Run once in an empty/new directory and return the saved report summary.

    Invalid destinations raise ValueError before changing existing content.
    Software/dependency/geometry failures instead create a nonpassing report.
    """
    output, reserved = _reserve(output_dir)
    started = time.monotonic(); manager = None
    report = {"schema_version": "0.1", "operation": "offline_self_check", "status": "needs_input", "verdict": "incomplete",
              "synthetic": True, "sample": {"pcb_dimensions_mm": [80, 55, 1.6], "hole_diameter_mm": 3., "hole_centers_xy_mm": [list(v) for v in _EXPECTED_HOLES]},
              "report_path": str(output / "report.json"), "output_dir": str(output), "checks": [], "issues": [], "geometry": {},
              "mounting_holes": [], "not_checked_scope": [], "execution": {"cad": "not_run", "independent_verification": "not_run",
              "slicing": "not_run", "model_provider": "not_run", "network": "not_run", "printing": "not_run"},
              "scope_notice": "Synthetic software regression only. Not arbitrary-board semantic recognition, physical fit, slicing, printer compatibility or print success acceptance.",
              "fingerprint_notice": "Quantized dimensions, mass properties and bore axes compare this fixture semantically; this is not byte equality or proof of full geometric congruence."}
    try:
        try:
            importlib.import_module("cadquery")
        except (ImportError, OSError):
            report["issues"].append({"code": "GEOMETRY_BACKEND_UNAVAILABLE", "message": "Install the pinned CAD backend before running the offline self-test."})
        else:
            report["runtime"] = {}
            for package in ("justbuild", "cadquery", "cadquery-ocp"):
                try: report["runtime"][package] = metadata.version(package)
                except metadata.PackageNotFoundError: report["runtime"][package] = "unavailable"
            manager = _OfflineManager(output / "jobs")
            job = create_demo(manager, wait=True)
            report.update(job_id=job.get("job_id"), job_path=str(manager.directory(job["job_id"]) / "job.json"))
            report["execution"]["cad"] = "run" if "generating" in job.get("stage_results", {}) else "not_run"
            report["execution"]["independent_verification"] = "run" if "verifying" in job.get("stage_results", {}) else "not_run"
            _assess(report, job, manager.directory(job["job_id"]))
            if report["checks"] and all(c["status"] in ("pass", "not_applicable") for c in report["checks"]):
                report.update(status="ok", verdict="pass")
            elif job.get("state") in ("needs_input", "unsupported", "review_incomplete"):
                report.update(status="needs_input", verdict="incomplete")
            else:
                report.update(status="failed", verdict="fail")
    except Exception as exc:
        report.update(status="failed", verdict="fail")
        report["issues"].append({"code": "SELF_CHECK_FAILED", "message": "Offline self-test did not complete: " + type(exc).__name__})
    finally:
        if manager is not None: manager.pool.shutdown(wait=True)
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(output / "report.json", report)
        reserved.unlink(missing_ok=True)
    return report
