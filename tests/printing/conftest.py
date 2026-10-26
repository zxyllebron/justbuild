from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from justbuild.printing.common import artifact, read_json, sha256, write_json


def cube_stl(path: Path) -> Path:
    vertices = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
                (0, 0, 2), (10, 0, 2), (10, 10, 2), (0, 10, 2)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    lines = ["solid synthetic_mm_fixture"]
    for face in faces:
        lines += ["facet normal 0 0 0", "outer loop"]
        lines += ["vertex " + " ".join(str(v) for v in vertices[index]) for index in face]
        lines += ["endloop", "endfacet"]
    lines += ["endsolid synthetic_mm_fixture"]
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def synthetic_cube():
    return cube_stl


@pytest.fixture
def fixture_job(tmp_path):
    profiles = {
        "machine": {"type": "machine", "name": "fixture A1 0.4", "printer_model": "Bambu Lab A1",
                    "nozzle_diameter": ["0.4"], "printable_area": ["0x0", "256x0", "256x256", "0x256"],
                    "printable_height": "256", "machine_start_gcode": "; fake", "machine_end_gcode": "; fake"},
        "process": {"type": "process", "name": "fixture process", "layer_height": "0.2", "wall_loops": "2",
                    "sparse_infill_density": "15%", "compatible_printers": ["fixture A1 0.4"]},
        "filament": {"type": "filament", "name": "fixture PLA", "filament_type": ["PLA"], "filament_diameter": ["1.75"],
                     "nozzle_temperature": ["220"], "compatible_printers": ["fixture A1 0.4"]},
    }
    refs = {}
    for role, data in profiles.items():
        path = write_json(tmp_path / (role + ".json"), data)
        refs[role] = {**artifact(role + "_profile", path), "id": data["name"]}
    request = write_json(tmp_path / "request.json", {
        "schema_version": "0.1", "job_id": "synthetic-fixture", "constraints": {"confirmed": True},
        "printing": {"printer": {"id": "synthetic-A1", "model": "Bambu Lab A1"},
                     "material": "PLA", "bed_type": "Textured PEI Plate", "nozzle_diameter_mm": 0.4, "profiles": refs},
    })
    artifacts = []
    for part in ("base", "lid"):
        step = tmp_path / f"{part}.step"
        step.write_text("NOT A REAL STEP; fake-slicer contract test only")
        mesh = cube_stl(tmp_path / f"{part}.stl")
        artifacts += [artifact(part + "_step", step, units="mm"), artifact(part + "_stl", mesh, units="mm")]
    cad = write_json(tmp_path / "cad.json", {"schema_version": "0.1", "job_id": "synthetic-fixture", "status": "ok", "artifacts": artifacts})
    verification = tmp_path / "verification.json"

    def rebind():
        previous = read_json(verification) if verification.exists() else {}
        return write_json(verification, {**previous, "schema_version": "0.1", "job_id": "synthetic-fixture",
                          "status": "ok", "verdict": "pass", "simulation": True,
                          "inputs": {"request": {"sha256": sha256(request)}, "cad_result": {"sha256": sha256(cad)}}})

    rebind()
    return {"root": tmp_path, "request": request, "cad": cad, "verification": verification, "rebind": rebind}
