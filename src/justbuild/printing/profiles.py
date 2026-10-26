"""Resolve BBL profile inheritance into explicitly pinned CLI inputs.

This utility reads an existing resource tree; it installs nothing and selects no
material or nozzle defaults. The flattened profiles are inputs for slicing, not
proof of compatibility with a physical printer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import PrintError, artifact, read_json, sha256, write_json


def export_profiles(resource_root: Path, output_dir: Path, *, machine: str,
                    process: str, filament: str, source_id: str) -> Path:
    output_dir = Path(output_dir).resolve()
    root = Path(resource_root).resolve()
    if not isinstance(source_id, str) or not source_id.strip():
        raise PrintError("PROFILE_SOURCE", "An explicit profile source identifier is required.")
    if any((output_dir / name).exists() for name in ("machine.json", "process.json", "filament.json", "profiles.json")):
        raise PrintError("OUTPUT_EXISTS", "Export profiles into a fresh directory.")
    sources: dict[str, str] = {}

    def expand(role: str, name: str, stack: tuple[str, ...] = ()) -> dict[str, Any]:
        if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
            raise PrintError("PROFILE_REFERENCE", "Profile names must be resource basenames.")
        key = f"{role}/{name}.json"
        if key in stack:
            raise PrintError("PROFILE_CYCLE", "Profile inheritance contains a cycle.")
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise PrintError("PROFILE_REFERENCE", "Profile reference leaves the resource root.")
        data = read_json(path)
        sources[key] = sha256(path)
        result: dict[str, Any] = {}
        if data.get("inherits"):
            result.update(expand(role, data["inherits"], stack + (key,)))
        includes = data.get("include", [])
        if not isinstance(includes, list):
            raise PrintError("PROFILE_REFERENCE", "Profile include must be a list.")
        for include in includes:
            result.update(expand(role, include, stack + (key,)))
        result.update(data)
        result.pop("include", None)
        result.pop("inherits", None)
        return result

    refs = {}
    for role, name in (("machine", machine), ("process", process), ("filament", filament)):
        data = expand(role, name)
        if data.get("type") != role:
            raise PrintError("PROFILE_TYPE", "Profile type does not match its selected role.")
        path = output_dir / f"{role}.json"
        if path.exists():
            raise PrintError("OUTPUT_EXISTS", "Export profiles into a fresh directory.")
        write_json(path, data)
        refs[role] = {**artifact(f"{role}_profile", path), "id": name}
    return write_json(output_dir / "profiles.json", {
        "schema_version": "0.1", "source_id": source_id, "profiles": refs,
        "source_files": [{"path": key, "sha256": value} for key, value in sorted(sources.items())],
        "method": "recursive parent, includes, then child; inheritance removed; no value tuning",
    })
