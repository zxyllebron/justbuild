"""Overlapping component subparts stay present without a fragile global Common."""
import json
from pathlib import Path

from justbuild.cad import generate
from justbuild.cad.kernel import read_step
from synthetic_fixture import make_fixture


def test_source_subparts_preserved_and_conservative_per_solid_measure_reported(tmp_path):
    import cadquery as cq
    request_path = make_fixture(tmp_path)
    request = json.loads(request_path.read_text())
    source = tmp_path / "synthetic_board.step"
    original = read_step(cq, source)
    a = cq.Solid.makeBox(2, 2, 1, cq.Vector(20, 20, 1.6))
    b = cq.Solid.makeBox(2, 2, 1, cq.Vector(20.5, 20, 1.6))
    assert a.intersect(b).Volume() > 0
    cq.exporters.export(cq.Compound.makeCompound([*original.Solids(), a, b]), str(source))
    result_path = generate(request_path, tmp_path / "generated")
    result = json.loads(result_path.read_text())
    assert result["status"] == "ok", result["issues"]
    item = next(a for a in result["artifacts"] if a["kind"] == "normalized_input_step")
    normalized = read_step(cq, result_path.parent / item["path"])
    assert len(normalized.Solids()) == len(result["metadata"]["input_solids"]) == 4
    for check in result["checks"]:
        if check["name"] in ("base_input_nonpenetration", "lid_input_nonpenetration", "service_envelope_empty"):
            measure = check["measurements"]
            assert measure["source_solid_count"] == 4
            assert "not a union volume" in measure["volume_measure"]
            assert measure["per_solid_intersections"] == [] or all(
                row["volume_mm3"] <= 1e-7 for row in measure["per_solid_intersections"])
