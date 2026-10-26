"""Small shared artifact helpers; geometry workers can remain independent."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return path


def resolve_artifact(result_path: Path, item: dict, root: Path | None = None) -> Path:
    raw = Path(item["path"])
    path = (result_path.parent / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if root is not None and not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact path escapes the job directory")
    if not path.is_file() or not item.get("sha256") or digest(path) != item["sha256"]:
        raise ValueError("Artifact is missing or its SHA-256 has changed")
    return path

