"""Synthetic-only geometry acceptance and fail-closed contract tests."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from justbuild.cad import generate
from synthetic_fixture import make_fixture


@pytest.fixture
def raw_request(tmp_path):
    request = json.loads(Path(__file__).with_name("example_request.json").read_text())
    (tmp_path / "synthetic_board.step").write_text("not read by validation-only tests")
    return request


def run(tmp_path, request):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    result_path = generate(path, tmp_path / "output")
    return result_path, json.loads(result_path.read_text())


@pytest.fixture
def synthetic(tmp_path):
    pytest.importorskip("cadquery")
    return json.loads(make_fixture(tmp_path).read_text())


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    pytest.importorskip("cadquery")
    root = tmp_path_factory.mktemp("cad-synthetic")
    request = json.loads(make_fixture(root).read_text())
    path, result = run(root, request)
    assert result["status"] == "ok", result["issues"]
    return path, result


def artifact(path, result, kind):
    item = next(a for a in result["artifacts"] if a["kind"] == kind)
    return path.parent / item["path"]


def shape(path):
    import cadquery as cq
    from justbuild.cad.kernel import read_step

    return read_step(cq, path)


def test_success_exports_hashed_real_solids_and_auditable_provenance(generated):
    path, result = generated
    assert {a["kind"] for a in result["artifacts"]} == {
        "base_step", "lid_step", "assembly_step", "normalized_input_step", "base_stl", "lid_stl",
        "review_glb", "source_step", "request_snapshot"}
    for a in result["artifacts"]:
        output = path.parent / a["path"]
        assert output.is_relative_to(path.parent)
        assert hashlib.sha256(output.read_bytes()).hexdigest() == a["sha256"]
    for kind, count in (("base_step", 1), ("lid_step", 1), ("assembly_step", 4), ("normalized_input_step", 2)):
        s = shape(artifact(path, result, kind))
        assert s.isValid() and len(s.Solids()) == count and s.Volume() > 0
    assert all(c["status"] in ("pass", "not_checked") for c in result["checks"])
    assert {c["name"] for c in result["checks"] if c["status"] == "not_checked"} == {
        "thermal", "assembly_path", "fastener_strength", "manufacturing"}
    meta = result["metadata"]
    assert meta["requires_independent_verification"] is True
    assert len(meta["input_solids"]) == 2
    assert meta["input_solids"][0]["bounds"]["max_mm"] == pytest.approx([60, 40, 1.6])
    assert meta["input_provenance"]["step_sha256"] == hashlib.sha256(artifact(path, result, "source_step").read_bytes()).hexdigest()


def test_screw_pilots_standoffs_and_counterbores_have_actual_material(generated):
    import cadquery as cq

    path, result = generated
    base, lid = (shape(artifact(path, result, f"{n}_step")) for n in ("base", "lid"))
    inside = lambda s, p: any(solid.isInside(cq.Vector(*p), 1e-6) for solid in s.Solids())
    assert not inside(base, [5, 5, -1])  # blind pilot, not a filled cylinder
    assert inside(base, [7, 5, -1])      # standoff annulus
    assert inside(base, [5, 5, -4.5])    # pilot is blind above the floor
    x, y, z = result["metadata"]["lid_screw_centers_mm"][0]
    assert not inside(base, [x, y, z-1])
    assert inside(base, [x+2, y, z-1])
    assert inside(base, [x, y, z-6.5])
    assert not inside(lid, [x, y, z+0.5])
    assert inside(lid, [x+2.5, y, z+0.5])
    assert not inside(lid, [x+2.5, y, z+2.5])  # head counterbore
    assert len(result["metadata"]["intentional_contacts"]) == 5


def test_openings_keepouts_and_plug_tool_boxes_are_empty_in_brep(generated):
    import cadquery as cq

    path, result = generated
    base, lid = (shape(artifact(path, result, f"{n}_step")) for n in ("base", "lid"))
    for port in result["metadata"]["ports"]:
        if port["access"] in ("internal", "covered"):
            continue
        b = port["service_envelope"]
        lo, hi = b["min_mm"], b["max_mm"]
        probe = cq.Solid.makeBox(*(h-l for l, h in zip(lo, hi)), cq.Vector(*lo))
        assert base.intersect(probe).Volume() == pytest.approx(0, abs=1e-7)
        assert lid.intersect(probe).Volume() == pytest.approx(0, abs=1e-7)
    assert not base.isInside(cq.Vector(20, -11, 2), 1e-6)  # actual keepout in front wall
    assert base.isInside(cq.Vector(30, -11, 2), 1e-6)      # surrounding wall remains
    assert not lid.isInside(cq.Vector(30, 20, 10), 1e-6)  # circular maintenance hole


def test_stl_print_positions_and_glb_units(generated):
    path, result = generated
    for kind in ("base_stl", "lid_stl"):
        data = artifact(path, result, kind).read_bytes()
        count = struct.unpack_from("<I", data, 80)[0]
        assert len(data) == 84+50*count and count > 100
        coords = [struct.unpack_from("<9f", data, 84+50*i+12) for i in range(count)]
        vertices = [v[j:j+3] for v in coords for j in (0, 3, 6)]
        assert [min(v[i] for v in vertices) for i in range(3)] == pytest.approx([0, 0, 0], abs=1e-5)
        assert max(v[0] for v in vertices) == pytest.approx(88)
        assert max(v[1] for v in vertices) == pytest.approx(64)
    glb = artifact(path, result, "review_glb").read_bytes()
    magic, version, length, size, kind = struct.unpack_from("<4sIII4s", glb)
    assert (magic, version, length, kind) == (b"glTF", 2, len(glb), b"JSON")
    document = json.loads(glb[20:20+size])
    root = document["nodes"][document["scenes"][document.get("scene", 0)]["nodes"][0]]
    assert root["scale"] == [0.001]*3
    assert root["rotation"] == pytest.approx([-2**-0.5, 0, 0, 2**-0.5])
    assert {n["name"] for n in document["nodes"]} >= {"base", "lid", "input_solid_0", "input_solid_1"}
    # Native positions are mm, scene transform yields an 88 mm = 0.088 m case.
    base_node = next(n for n in document["nodes"] if n.get("name") == "base")
    pos = [document["accessors"][p["attributes"]["POSITION"]]
           for p in document["meshes"][base_node["mesh"]]["primitives"]]
    assert (max(a["max"][0] for a in pos)-min(a["min"][0] for a in pos))*root["scale"][0] == pytest.approx(0.088)
    assert any(m.get("alphaMode") == "BLEND" for m in document["materials"])


@pytest.mark.parametrize("change", [
    lambda r: r["constraints"].update(confirmed=False),
    lambda r: r["constraints"].pop("feature_review"),
    lambda r: r["constraints"]["feature_review"].update(ports_complete=False),
    lambda r: r["constraints"].update(mounting_holes=[]),
    lambda r: r["constraints"]["ports"][0].pop("service_envelope"),
    lambda r: r["constraints"]["manufacturing"].pop("clearance_bottom_mm"),
    lambda r: r["board"]["metadata"].pop("pcb_selection_source"),
    lambda r: r["constraints"]["board_frame"].update(y_axis=[0, -1, 0]),
    lambda r: r["constraints"]["ports"][0].update(insertion_direction=[1, 0, 0]),
    lambda r: r["constraints"]["manufacturing"].update(wall_thickness_mm=True),
    lambda r: r["constraints"]["manufacturing"].update(boolean_volume_tolerance_mm3=100),
    lambda r: r["constraints"]["ports"][0]["service_envelope"].update(min_mm=[64, 30, 1.6], max_mm=[100, 35, 6.6]),
])
def test_missing_or_invalid_mechanical_requirements_fail_before_kernel(tmp_path, raw_request, change):
    change(raw_request)
    _, result = run(tmp_path, raw_request)
    assert result["status"] == "needs_input", result["issues"]
    assert result["artifacts"] == []
    assert "kernel" not in result["metadata"]


@pytest.mark.parametrize("section,field,value", [
    ("ports", "shape", "rounded_rectangle"), ("keepouts", "shape", "cylinder"),
    ("mounting_holes", "axis", [1, 0, 0]),
])
def test_unsupported_primitives_are_explicit(tmp_path, raw_request, section, field, value):
    raw_request["constraints"][section][0][field] = value
    _, result = run(tmp_path, raw_request)
    assert result["status"] == "unsupported"
    assert result["artifacts"] == []


def test_protected_support_cannot_be_cut_or_moved(tmp_path, synthetic):
    synthetic["constraints"]["keepouts"] = [{"id": "bad", "shape": "box", "purpose": "no_material",
        "min_mm": [4, 4, -3], "max_mm": [6, 6, -1], "source": "synthetic conflict"}]
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "failed"
    assert result["issues"][-1]["code"] == "PROTECTED_FEATURE_CONFLICT"
    assert result["issues"][-1]["evidence"]["protected_feature_id"] == "h1"
    assert result["artifacts"] == []


def test_unknown_plug_size_is_not_silently_enlarged_or_ignored(tmp_path, synthetic):
    synthetic["constraints"]["ports"][0]["service_envelope"].update(min_mm=[64, 2, 1.6], max_mm=[100, 38, 6.6])
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "failed"
    assert any(c["name"] == "service_envelope_empty" and c["status"] == "fail" for c in result["checks"])
    assert result["artifacts"] == []


def test_component_collision_with_standoff_is_detected(tmp_path, synthetic):
    import cadquery as cq

    original = shape(tmp_path / "synthetic_board.step")
    obstruction = cq.Solid.makeBox(2, 2, 2, cq.Vector(4, 4, -2))
    cq.exporters.export(cq.Compound.makeCompound([*original.Solids(), obstruction]), str(tmp_path / "synthetic_board.step"))
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "failed"
    assert any(c["name"] == "base_input_nonpenetration" and c["status"] == "fail" for c in result["checks"])


def test_extra_unreviewed_pcb_hole_is_not_approximated(tmp_path, synthetic):
    import cadquery as cq

    pcb, connector = shape(tmp_path / "synthetic_board.step").Solids()
    pcb = pcb.cut(cq.Solid.makeCylinder(1, 3, cq.Vector(20, 20, -0.5)))
    cq.exporters.export(cq.Compound.makeCompound([pcb, connector]), str(tmp_path / "synthetic_board.step"))
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "unsupported"
    assert result["issues"][-1]["code"] == "UNSUPPORTED_PCB_GEOMETRY"


def test_rigid_board_frame_transform_preserves_actual_geometry(tmp_path, synthetic, generated):
    import cadquery as cq

    frame = {"origin_mm": [10, 20, 30], "x_axis": [0, 1, 0], "y_axis": [0, 0, 1], "z_axis": [1, 0, 0]}
    transform = cq.Plane(origin=(10, 20, 30), xDir=(0, 1, 0), normal=(1, 0, 0)).rG
    local = shape(tmp_path / "synthetic_board.step")
    cq.exporters.export(local.transformShape(transform), str(tmp_path / "synthetic_board.step"))
    synthetic["constraints"]["board_frame"] = frame
    path, result = run(tmp_path, synthetic)
    assert result["status"] == "ok", result["issues"]
    baseline_path, baseline = generated
    for kind in ("base_step", "lid_step", "normalized_input_step"):
        a = shape(artifact(path, result, kind))
        b = shape(artifact(baseline_path, baseline, kind))
        assert a.cut(b).Volume()+b.cut(a).Volume() < 1e-7


def test_all_step_roots_are_preserved(tmp_path, synthetic):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs, STEPControl_Reader

    solids = shape(tmp_path / "synthetic_board.step").Solids()
    writer = STEPControl_Writer()
    for s in solids:
        assert writer.Transfer(s.wrapped, STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(tmp_path / "synthetic_board.step")) == IFSelect_RetDone
    reader = STEPControl_Reader()
    reader.ReadFile(str(tmp_path / "synthetic_board.step"))
    assert reader.NbRootsForTransfer() == 2
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "ok", result["issues"]
    assert len(result["metadata"]["input_solids"]) == 2


def test_source_step_encoded_in_meters_is_converted_to_mm(tmp_path, synthetic, generated):
    import cadquery as cq

    source = tmp_path / "synthetic_board.step"
    scaled = shape(source).scale(0.001)
    cq.exporters.export(scaled, str(source))
    raw = source.read_text()
    assert "SI_UNIT(.MILLI.,.METRE.)" in raw
    source.write_text(raw.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)"))
    path, result = run(tmp_path, synthetic)
    assert result["status"] == "ok", result["issues"]
    baseline_path, baseline = generated
    actual = shape(artifact(path, result, "normalized_input_step"))
    expected = shape(artifact(baseline_path, baseline, "normalized_input_step"))
    assert actual.cut(expected).Volume()+expected.cut(actual).Volume() < 1e-7


def test_dangling_surface_is_not_silently_dropped(tmp_path, synthetic):
    import cadquery as cq

    solid = shape(tmp_path / "synthetic_board.step")
    face = cq.Face.makePlane(3, 3, cq.Vector(20, 20, 20))
    cq.exporters.export(cq.Compound.makeCompound([*solid.Solids(), face]), str(tmp_path / "synthetic_board.step"))
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "unsupported"
    assert result["issues"][-1]["code"] == "UNSUPPORTED_STEP"


@pytest.mark.parametrize("face,center,direction,lo,hi", [
    ("x_min", [0, 20, 0.8], [1, 0, 0], [-30, 18, 0.3], [0, 22, 1.3]),
    ("x_max", [64, 20, 4.1], [-1, 0, 0], [64, 18, 3.6], [100, 22, 4.6]),
    ("y_min", [30, 0, 0.8], [0, 1, 0], [28, -30, 0.3], [32, 0, 1.3]),
    ("y_max", [30, 40, 0.8], [0, -1, 0], [28, 40, 0.3], [32, 80, 1.3]),
    ("z_min", [30, 20, 0], [0, 0, 1], [28, 18, -30], [32, 22, 0]),
    ("z_max", [30, 20, 1.6], [0, 0, -1], [28, 18, 1.6], [32, 22, 30]),
])
def test_six_cardinal_faces_have_real_openings(tmp_path, synthetic, face, center, direction, lo, hi):
    synthetic["constraints"]["ports"] = [{"id": "face_test", "access": "external", "shape": "rectangle",
        "face": face, "center_mm": center, "width_mm": 8, "height_mm": 8,
        "insertion_direction": direction, "source": "synthetic face test",
        "service_envelope": {"min_mm": lo, "max_mm": hi, "source": "synthetic face test"}}]
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "ok", result["issues"]
    assert any(c["name"] == "service_envelope_empty" and c["status"] == "pass" for c in result["checks"])


def test_malformed_json_and_missing_kernel_have_structured_results(tmp_path, synthetic, monkeypatch):
    import sys

    bad = tmp_path / "bad.json"
    bad.write_text('{"job_id": "bad", "x": NaN}')
    result = json.loads(generate(bad, tmp_path / "output").read_text())
    assert result["status"] == "failed" and result["artifacts"] == []
    monkeypatch.setitem(sys.modules, "cadquery", None)
    _, result = run(tmp_path, synthetic)
    assert result["status"] == "unsupported"
    assert result["issues"][-1]["code"] == "CAD_DEPENDENCY_UNAVAILABLE"


def test_repeated_runs_preserve_prior_result(generated):
    path, _ = generated
    prior = path.read_bytes()
    # Even an invalid second request receives a fresh directory in the same output root.
    second = generate(path.parent / "missing-request.json", path.parent.parent)
    assert second.parent != path.parent
    assert path.read_bytes() == prior
    assert json.loads(second.read_text())["artifacts"] == []
