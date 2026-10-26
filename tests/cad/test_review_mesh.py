"""A lighter display mesh must preserve printable STL and authoritative solids."""
import json
import struct
from pathlib import Path

import pytest

from justbuild.cad import generate
from justbuild.cad.request import CadError, validate
from synthetic_fixture import make_fixture


def artifact(path, result, kind):
    return path.parent / next(a["path"] for a in result["artifacts"] if a["kind"] == kind)


def glb_triangles(path):
    raw = path.read_bytes()
    length = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20+length])
    return sum(document["accessors"][p["indices"]]["count"] // 3
               for mesh in document["meshes"] for p in mesh["primitives"])


def test_review_mesh_changes_actual_glb_without_changing_stl_resolution_or_brep(tmp_path):
    import cadquery as cq
    from justbuild.cad.kernel import read_step
    request_path = make_fixture(tmp_path)
    request = json.loads(request_path.read_text())
    m = request["constraints"]["manufacturing"]
    m.update(mesh_linear_tolerance_mm=.05, mesh_angular_tolerance_rad=.12)
    results = []
    for linear, angular in ((.01, .05), (.25, .4)):
        m.update(review_mesh_linear_tolerance_mm=linear, review_mesh_angular_tolerance_rad=angular)
        request_path.write_text(json.dumps(request))
        path = generate(request_path, tmp_path / "runs")
        result = json.loads(path.read_text())
        assert result["status"] == "ok", result["issues"]
        display = result["metadata"]["display_mesh"]
        assert display["linear_tolerance_mm"] == linear
        assert display["angular_tolerance_rad"] == angular
        assert display["stl_linear_tolerance_mm"] == .05
        assert display["stl_angular_tolerance_rad"] == .12
        assert display["authoritative_geometry"] is False
        assert display["linear_deflection_mode"] == "absolute_mm"
        progress = json.loads((path.parent / "progress.json").read_text())
        assert progress["phase"] == "finished" and progress["status"] == "ok"
        results.append((path, result))
    for kind in ("base_stl", "lid_stl"):
        # The actual binary STL counts remain tied to the print tolerances.
        triangles = [struct.unpack_from("<I", artifact(p, r, kind).read_bytes(), 80)[0]
                     for p, r in results]
        assert triangles[0] == triangles[1] and triangles[0] > 100
    glb_counts = [glb_triangles(artifact(p, r, "review_glb")) for p, r in results]
    assert glb_counts[1] < glb_counts[0]
    for kind in ("base_step", "lid_step"):
        a, b = [read_step(cq, artifact(p, r, kind)) for p, r in results]
        assert a.cut(b).Volume() + b.cut(a).Volume() < 1e-7


@pytest.mark.parametrize("field", ["review_mesh_linear_tolerance_mm", "review_mesh_angular_tolerance_rad"])
@pytest.mark.parametrize("value", [0, -1, True, float("inf"), 1.01])
def test_invalid_review_tolerances_rejected_before_geometry(tmp_path, field, value):
    request = json.loads(Path(__file__).with_name("example_request.json").read_text())
    (tmp_path / "synthetic_board.step").write_text("validation-only placeholder")
    request["constraints"]["manufacturing"][field] = value
    with pytest.raises(CadError):
        validate(request, tmp_path)
