"""Small, dependency-free helpers for the printing artifact contract."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PrintError(Exception):
    def __init__(self, code: str, message: str, status: str = "failed"):
        super().__init__(message)
        self.code, self.status = code, status


class OutputOverlapError(ValueError):
    """No result can be written safely at the caller-selected destination."""


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrintError("INVALID_JSON", "A required JSON document is unreadable or invalid.") from exc
    if not isinstance(result, dict):
        raise PrintError("INVALID_JSON", "A JSON document must be an object.")
    return result


def write_json(path: Path, value: dict[str, Any]) -> Path:
    """Atomic, durable replacement; caller controls the output directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".printing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path.resolve()


def protect_output(output_dir: Path, filename: str, inputs: list[Path]) -> None:
    """A mistaken output directory must not replace a source document/artifact."""
    destination = (output_dir / filename).resolve()
    if any(destination == Path(path).resolve() for path in inputs):
        raise OutputOverlapError("Printing result path overlaps a protected input; choose another output directory.")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise PrintError("INVALID_HASH", "A lowercase SHA-256 digest is required.")
    return value


def text_field(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise PrintError("MISSING_FIELD", f"A nonempty {name} is required.", "needs_input")
    return value


def positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise PrintError("INVALID_NUMBER", f"{name} must be a positive finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PrintError("INVALID_NUMBER", f"{name} must be a positive finite number.") from exc
    if not math.isfinite(number) or number <= 0:
        raise PrintError("INVALID_NUMBER", f"{name} must be a positive finite number.")
    return number


def resolve_path(value: Any, document: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise PrintError("MISSING_PATH", "An artifact path is required.")
    path = Path(value)
    return (path if path.is_absolute() else document.parent / path).resolve()


def checked_file(ref: dict[str, Any], document: Path) -> Path:
    if not isinstance(ref, dict):
        raise PrintError("INVALID_ARTIFACT", "An artifact must be an object.")
    path = resolve_path(ref.get("path"), document)
    expected = digest(ref.get("sha256"))
    if not path.is_file():
        raise PrintError("MISSING_ARTIFACT", "A required artifact file is missing.", "needs_input")
    if sha256(path) != expected:
        raise PrintError("HASH_MISMATCH", "Artifact bytes differ from their pinned SHA-256.")
    return path


def artifact(kind: str, path: Path, *, units: str | None = None) -> dict[str, Any]:
    result = {"kind": kind, "path": str(path.resolve()), "sha256": sha256(path)}
    if units:
        result["units"] = units
    return result


def envelope(job_id: str = "unknown") -> dict[str, Any]:
    return {"schema_version": "0.1", "job_id": job_id, "status": "ok", "artifacts": [], "issues": []}


def record_error(result: dict[str, Any], exc: PrintError) -> None:
    result["status"] = exc.status
    result["issues"].append({"code": exc.code, "severity": "error", "message": str(exc), "evidence": {}})


def require_envelope(document: dict[str, Any], job_id: str) -> None:
    if document.get("schema_version") != "0.1" or document.get("job_id") != job_id:
        raise PrintError("DOCUMENT_MISMATCH", "Schema version or job identity does not match.")
