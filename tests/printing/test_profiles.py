from __future__ import annotations

import pytest

from justbuild.printing import export_profiles
from justbuild.printing.common import PrintError, read_json, sha256, write_json


def resources(tmp_path):
    root = tmp_path / "resources"
    for role in ("machine", "process", "filament"):
        write_json(root / role / "base.json", {"type": role, "name": "base", "parent_key": "preserved", "overridden": 1})
        write_json(root / role / "template.json", {"name": "template", "machine_start_gcode": "; included"})
        write_json(root / role / "child.json", {"type": role, "name": "child", "inherits": "base",
                                               "include": ["template"], "overridden": 2})
    return root


def test_profile_resolution_preserves_templates_and_provenance(tmp_path):
    root = resources(tmp_path)
    result = export_profiles(root, tmp_path / "export", machine="child", process="child", filament="child", source_id="synthetic")
    manifest = read_json(result)
    assert len(manifest["source_files"]) == 9
    for role in ("machine", "process", "filament"):
        ref = manifest["profiles"][role]
        data = read_json(tmp_path / "export" / (role + ".json"))
        assert data["parent_key"] == "preserved" and data["overridden"] == 2
        assert data["machine_start_gcode"] == "; included"
        assert "inherits" not in data and "include" not in data
        assert sha256(tmp_path / "export" / (role + ".json")) == ref["sha256"]


def test_profile_cycles_are_rejected(tmp_path):
    root = resources(tmp_path)
    write_json(root / "machine" / "base.json", {"type": "machine", "inherits": "child"})
    with pytest.raises(PrintError) as error:
        export_profiles(root, tmp_path / "export", machine="child", process="child", filament="child", source_id="synthetic")
    assert error.value.code == "PROFILE_CYCLE"


def test_profile_paths_cannot_leave_resource_tree(tmp_path):
    root = resources(tmp_path)
    with pytest.raises(PrintError) as error:
        export_profiles(root, tmp_path / "export", machine="../../outside", process="child", filament="child", source_id="synthetic")
    assert error.value.code == "PROFILE_REFERENCE"


def test_profile_export_does_not_overwrite_existing_output(tmp_path):
    root = resources(tmp_path)
    out = tmp_path / "export"
    write_json(out / "process.json", {"protected": True})
    with pytest.raises(PrintError) as error:
        export_profiles(root, out, machine="child", process="child", filament="child", source_id="synthetic")
    assert error.value.code == "OUTPUT_EXISTS"
    assert not (out / "machine.json").exists()
