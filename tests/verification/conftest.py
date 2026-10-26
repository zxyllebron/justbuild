"""Owner 03 fixtures; all generated STEP/JSON files go into pytest tmp_path."""

import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


@pytest.fixture
def case(tmp_path):
    cq = pytest.importorskip("cadquery")
    from justbuild.verification import verify
    from justbuild.verification.geometry import box
    from justbuild.verification.engine import CORE_SCOPES

    class Case:
        def __init__(self):
            self.root = tmp_path
            self.shapes = {
                "board": box([2, 2, 2], [8, 8, 3]),
                "base": box([0, 0, 0], [10, 10, 6]).cut(box([1, 1, 1], [9, 9, 7])),
                "lid": box([0, 0, 6.2], [10, 10, 7.2]),
            }
            self.request = {
                "schema_version": "0.1", "job_id": "synthetic-03",
                "board": {"step_path": "board.step", "units": "mm"},
                "constraints": {
                    "confirmed": True, "structure": "single_pcb_screw_lid",
                    "board_frame": {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0],
                                    "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
                    "mounting_holes": [], "ports": [], "keepouts": [],
                },
                "verification": {
                    "geometry_frame": "confirmed_board_frame",
                    "coverage": {name: {"complete": True, "provenance": "synthetic fixture specification"}
                                 for name in CORE_SCOPES},
                    "clearances": [{"parts": ["board", "base"], "minimum_mm": 0.5,
                                    "provenance": "fixture requirement"}],
                    "assembly_checks": [
                        {"id": "board-out", "moving_part": "board", "translation_mm": [0, 0, 20],
                         "envelope": {"kind": "box", "min_mm": [2, 2, 2], "max_mm": [8, 8, 3],
                                      "provenance": "synthetic PCB rectangular solid"},
                         "against": ["base"], "absent_parts": ["lid"],
                         "stage_provenance": "lid removed before PCB extraction",
                         "minimum_clearance_mm": 0.5, "provenance": "fixture board extraction"},
                        {"id": "lid-out", "moving_part": "lid", "translation_mm": [0, 0, 20],
                         "envelope": {"kind": "box", "min_mm": [0, 0, 6.2], "max_mm": [10, 10, 7.2],
                                      "provenance": "synthetic lid rectangular solid"},
                         "against": ["base", "board"], "minimum_clearance_mm": 0,
                         "provenance": "fixture lid extraction"}],
                },
            }
            self.cad = {"schema_version": "0.1", "job_id": "synthetic-03", "status": "ok",
                        "artifacts": [], "issues": []}

        @property
        def options(self):
            return self.request["verification"]

        def write(self):
            self.cad["artifacts"] = []
            for part, shape in self.shapes.items():
                path = self.root / f"{part}.step"
                cq.exporters.export(shape, str(path))
                if part != "board":
                    self.cad["artifacts"].append({"kind": f"{part}_step", "path": path.name,
                                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                                  "units": "mm"})
            self.request_path = self.root / "request.json"
            self.cad_path = self.root / "cad.json"
            self.request_path.write_text(json.dumps(self.request))
            self.cad_path.write_text(json.dumps(self.cad))

        def run(self):
            self.write()
            return self.verify_written()

        def verify_written(self):
            self.result_path = verify(self.request_path, self.cad_path, self.root / "out")
            return json.loads(self.result_path.read_text())

        def bore(self, dx=0, diameter=2.0, span=(0, 1), max_diameter=None):
            from justbuild.verification.geometry import cylinder
            center = [4, 4, 0]
            tool = cylinder([center[0] + dx, center[1], 0], [0, 0, 1], diameter, list(span))
            self.shapes["base"] = self.shapes["base"].cut(tool)
            self.shapes["board"] = self.shapes["board"].cut(cylinder(center, [0, 0, 1], 2, [1, 4]))
            self.request["constraints"]["mounting_holes"] = [
                {"id": "h1", "center_mm": center, "axis": [0, 0, 1], "diameter_mm": 2,
                 "provenance": "confirmed synthetic board drawing"}]
            target = {"part_id": "base", "span_mm": [0, 1], "minimum_diameter_mm": 2,
                      "position_tolerance_mm": 0.05, "angle_tolerance_deg": 0.1,
                      "provenance": "synthetic fastening requirement"}
            if max_diameter is not None:
                target["maximum_diameter_mm"] = max_diameter
            self.options["mounting_checks"] = {"h1": [target]}

        def port(self, opened=True):
            envelope = {"kind": "box", "min_mm": [-1, 4, 3.5], "max_mm": [2, 6, 4.5],
                        "provenance": "confirmed connector opening drawing"}
            self.request["constraints"]["ports"] = [
                {"id": "usb", "access_mode": "external", "insertion_direction": [1, 0, 0]}]
            self.options["port_checks"] = {"usb": {
                "opening_envelope": envelope, "against": ["base", "lid"], "minimum_clearance_mm": 0,
                "insertion": {"envelope": dict(envelope), "translation_mm": [-5, 0, 0],
                              "against": ["base", "lid", "board"], "minimum_clearance_mm": 0,
                              "provenance": "confirmed rigid plug withdrawal path"}}}
            if opened:
                self.shapes["base"] = self.shapes["base"].cut(box(envelope["min_mm"], envelope["max_mm"]))

    return Case()
