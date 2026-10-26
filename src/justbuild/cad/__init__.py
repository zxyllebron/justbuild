"""Single-PCB, base/lid CAD generation; all files are written under output_dir.

CadQuery is loaded lazily so input failures and missing dependencies still produce
structured results. See docs/cad.md for the constrained mechanical dialect.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .request import CadError, validate

__all__ = ["generate"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(request_path: Path, output_dir: Path, *, source_validation_cache: dict | None = None) -> Path:
    """Return a fresh cad_result.json path, including on invalid input/kernel failure.

    Failure to create/write output_dir raises OSError because no result can be
    persisted. Each invocation has its own immutable-by-convention run directory.
    """
    request_path, output_dir = Path(request_path).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="cad-", dir=output_dir))
    def progress(phase: str, **details) -> None:
        value = {"schema_version": "0.1", "phase": phase,
                 "updated_at": datetime.now(timezone.utc).isoformat(), **details}
        temporary = out / "progress.json.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(out / "progress.json")
    progress("validating_request")
    result = {"schema_version": "0.1", "job_id": None, "status": "failed", "artifacts": [], "issues": [],
              "checks": [], "metadata": {"units": "mm", "module": "cad", "scope": "generation_and_local_BRep_checks",
                "input_provenance": {"request_path": str(request_path)},
                "requires_independent_verification": True}}
    try:
        raw = request_path.read_bytes()
        (out / "request_snapshot.json").write_bytes(raw)
        provenance = result["metadata"]["input_provenance"]
        provenance["request_sha256"] = hashlib.sha256(raw).hexdigest()
        request = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON number {value}")))
        if isinstance(request, dict) and isinstance(request.get("job_id"), str):
            result["job_id"] = request["job_id"]
        config = validate(request, request_path.parent)
        config["_progress"] = progress
        config["_source_validation_cache"] = source_validation_cache
        source = out / "source.step"
        shutil.copyfile(config["step_path"], source)
        provenance.update({"step_path": str(config["step_path"]), "step_sha256": sha256(source),
                           "consumed_step_snapshot": source.name, "step_size_bytes": source.stat().st_size,
                           "selection_source": request["board"]["metadata"]["pcb_selection_source"]})
        from .kernel import build
        artifacts = build(config, source, out, result)
        artifacts.extend([("source_step", source, None), ("request_snapshot", out / "request_snapshot.json", None)])
        result["artifacts"] = [{"kind": kind, "path": path.name, "sha256": sha256(path),
                                **({"units": units} if units else {})} for kind, path, units in artifacts]
        result["status"] = "ok"
        for name, message in (
            ("assembly_path", "Only final-pose nonpenetration was checked; no assembly-path proof."),
            ("thermal", "No thermal load, ventilation requirement or material temperature suitability was verified."),
            ("fastener_strength", "Pilot/hardware dimensions are caller-supplied; torque, pullout and screw-length fit are unverified."),
            ("manufacturing", "No slicer, printer calibration, mesh watertightness or physical-fit acceptance was performed."),
        ):
            result["checks"].append({"name": name, "status": "not_checked", "method": "not performed",
                                     "tolerance": None, "measurements": {}})
            result["issues"].append({"code": f"{name.upper()}_NOT_CHECKED", "severity": "warning",
                                    "message": message, "evidence": {}})
    except CadError as exc:
        result["status"] = exc.status
        result["issues"].append({"code": exc.code, "severity": "error", "message": str(exc),
                                 **({"feature_id": exc.feature_id} if exc.feature_id else {}), "evidence": exc.evidence})
    except (ImportError, ModuleNotFoundError) as exc:
        result["status"] = "unsupported"
        result["issues"].append({"code": "CAD_DEPENDENCY_UNAVAILABLE", "severity": "error", "message": str(exc),
                                 "evidence": {"required": "cadquery==2.6.1"}})
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result["issues"].append({"code": "CAD_GENERATION_FAILED", "severity": "error",
                                 "message": f"{type(exc).__name__}: {exc}", "evidence": {}})
    except Exception as exc:
        # OCC exceptions vary by version. No partial artifact is advertised on failure.
        result["issues"].append({"code": "CAD_KERNEL_FAILED", "severity": "error",
                                 "message": f"{type(exc).__name__}: {exc}", "evidence": {}})
    destination = out / "cad_result.json"
    temporary = out / "cad_result.json.tmp"
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(destination)
    progress("finished", status=result["status"], result_filename=destination.name)
    # A source-only optional export failure must not require a manual recovery
    # command when the validated original is already in the exact output frame.
    # The recovery function rechecks every eligibility condition and writes a
    # separate fresh result; all other failures remain failures.
    failures = [c for c in result["checks"] if c.get("status") == "fail"]
    if (result["status"] == "failed" and len(failures) == 1
            and failures[0].get("name") == "normalized_input_step_roundtrip"):
        try:
            from .recovery import resume_identity_source_preservation
            return resume_identity_source_preservation(request_path, destination, output_dir)
        except (CadError, OSError, ValueError, RuntimeError) as exc:
            progress("finished", status="failed", result_filename=destination.name,
                     recovery_not_applied=True, recovery_error_type=type(exc).__name__)
    return destination
