"""Opt-in local slicing only. Never discovers or accesses a printer/account."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from justbuild.printing import BambuCliSlicer, export_profiles
from justbuild.printing.common import read_json, write_json


@pytest.mark.skipif(not os.environ.get("JUSTBUILD_BAMBU_BINARY"), reason="Existing pinned Bambu CLI not configured")
def test_installed_bambu_a1_cli_without_display(tmp_path, synthetic_cube):
    executable = Path(os.environ["JUSTBUILD_BAMBU_BINARY"])
    resources = Path(os.environ["JUSTBUILD_BBL_RESOURCES"])
    manifest = read_json(export_profiles(resources, tmp_path / "profiles",
                         machine="Bambu Lab A1 0.4 nozzle", process="0.20mm Standard @BBL A1",
                         filament="Bambu PLA Basic @BBL A1", source_id="installed v02.08.02.61 synthetic fixture"))
    profiles = {role: Path(ref["path"]) for role, ref in manifest["profiles"].items()}
    environment = {"LD_LIBRARY_PATH": str(executable.parent), "LC_ALL": "zh_CN.UTF-8"}
    if os.environ.get("JUSTBUILD_BAMBU_LOCALE_DIR"):
        environment["LOCPATH"] = os.environ["JUSTBUILD_BAMBU_LOCALE_DIR"]
    slicer = BambuCliSlicer(executable,
                           executable_sha256="d267ad0b4589c46cb18f49e2414f87a529b446dc992cb6ab8a728fe43f549eb8",
                           version="02.08.02.61", environment=environment, timeout_seconds=90)
    result = slicer.slice([synthetic_cube(tmp_path / "base.stl"), synthetic_cube(tmp_path / "lid.stl")], profiles,
                          {"bed_type": "Textured PEI Plate"}, tmp_path / "slice")
    assert result.simulation is False
    assert result.metadata["inspection"]["gcode_members"] == ["Metadata/plate_1.gcode"]
    with zipfile.ZipFile(result.path) as archive:
        assert archive.testzip() is None
        assert len(archive.read("Metadata/plate_1.gcode")) > 10000
        assert len([name for name in archive.namelist() if name.startswith("3D/Objects/")]) == 2
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        assert settings["printer_model"] == "Bambu Lab A1"
        assert settings["nozzle_diameter"] == ["0.4"]
        assert settings["filament_type"] == ["PLA"]
    write_json(tmp_path / "slice_evidence.json", {"slicer": result.metadata,
                                                "live_printer_access": False, "physical_validation": "not_checked"})
