"""Trusted server-side configuration for an optional, preinstalled slicer."""
from __future__ import annotations

import os
from pathlib import Path

from .contracts import read_json


class SlicingConfig:
    def __init__(self):
        self.slicer = None
        self.resources = None
        required = ("JUSTBUILD_BAMBU_BINARY", "JUSTBUILD_BAMBU_SHA256", "JUSTBUILD_BAMBU_VERSION", "JUSTBUILD_BBL_RESOURCES")
        if not all(os.environ.get(key) for key in required):
            return
        from .printing import BambuCliSlicer
        executable = Path(os.environ["JUSTBUILD_BAMBU_BINARY"]).resolve()
        self.resources = Path(os.environ["JUSTBUILD_BBL_RESOURCES"]).resolve()
        environment = {"LD_LIBRARY_PATH":str(executable.parent),"LC_ALL":"zh_CN.UTF-8"}
        if os.environ.get("JUSTBUILD_BAMBU_LOCALE_DIR"):
            environment["LOCPATH"] = os.environ["JUSTBUILD_BAMBU_LOCALE_DIR"]
        self.slicer = BambuCliSlicer(executable, executable_sha256=os.environ["JUSTBUILD_BAMBU_SHA256"],
                                   version=os.environ["JUSTBUILD_BAMBU_VERSION"], environment=environment,timeout_seconds=120)

    def synthetic_settings(self, job_dir: Path) -> dict | None:
        if self.slicer is None:
            return None
        from .printing import export_profiles
        manifest = read_json(export_profiles(self.resources, job_dir / "demo-profiles",
                             machine="Bambu Lab A1 0.4 nozzle",process="0.20mm Standard @BBL A1",
                             filament="Bambu PLA Basic @BBL A1",source_id="installed official profiles; synthetic slicing reference only"))
        return {"printer":{"id":"synthetic-a1-offline","model":"Bambu Lab A1"},"nozzle_diameter_mm":.4,
                "material":"PLA","bed_type":"Textured PEI Plate","profiles":manifest["profiles"],
                "source":"synthetic slicing configuration, not a material recommendation or live printer binding"}

