"""Original PCB geometry remains authoritative for contour, holes and support."""
import json
from pathlib import Path

import pytest

from justbuild.cad import generate
from justbuild.cad.kernel import read_step
from synthetic_fixture import make_fixture


def fixture(tmp_path):
    import cadquery as cq
    request = json.loads(make_fixture(tmp_path).read_text())
    source = tmp_path / "synthetic_board.step"
    pcb, connector = read_step(cq, source).Solids()
    # An edge notch, a nonmount via and a slot remain in the original PCB and
    # are deliberately not converted into fictitious mounting requirements.
    pcb = pcb.cut(cq.Solid.makeBox(8, 5, 3, cq.Vector(26, 36, -.5)))
    pcb = pcb.cut(cq.Solid.makeCylinder(.3, 3, cq.Vector(20, 20, -.5)))
    pcb = pcb.cut(cq.Solid.makeBox(4, 1, 3, cq.Vector(30, 28, -.5)))
    cq.exporters.export(cq.Compound.makeCompound([pcb, connector]), str(source))
    request["board"]["metadata"]["pcb_geometry_mode"] = "original_outline"
    return request, source


def run(tmp_path, request):
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request))
    result_path = generate(source, tmp_path / "generated")
    return result_path, json.loads(result_path.read_text())


def test_notch_slot_and_nonmount_via_preserved_with_real_support_checks(tmp_path):
    import cadquery as cq
    request, source = fixture(tmp_path)
    path, result = run(tmp_path, request)
    assert result["status"] == "ok", result["issues"]
    check = next(c for c in result["checks"] if c["name"] == "original_pcb_constant_section")
    assert check["status"] == "pass"
    assert check["measurements"]["underside_inner_wire_count"] == 6
    assert check["measurements"]["symmetric_difference_mm3"] < 1e-7
    supports = [c for c in result["checks"] if c["name"] == "mounting_support_footprint_within_original_pcb"]
    assert len(supports) == 4 and all(c["status"] == "pass" for c in supports)
    original = read_step(cq, source)
    normalized = path.parent / next(a["path"] for a in result["artifacts"] if a["kind"] == "normalized_input_step")
    restored = read_step(cq, normalized)
    assert len(restored.Solids()) == len(original.Solids())
    assert original.cut(restored).Volume() + restored.cut(original).Volume() < 1e-7


@pytest.mark.parametrize("diameter_delta", [-.05, .05])
def test_wrong_hole_diameter_is_rejected_against_original_material(tmp_path, diameter_delta):
    request, _ = fixture(tmp_path)
    request["constraints"]["mounting_holes"][0]["diameter_mm"] += diameter_delta
    _, result = run(tmp_path, request)
    assert result["status"] == "failed"
    assert result["artifacts"] == []
    assert any(c["status"] == "fail" and c.get("feature_id") == "h1" for c in result["checks"])


def test_shifted_hole_is_not_moved_or_accepted(tmp_path):
    request, _ = fixture(tmp_path)
    request["constraints"]["mounting_holes"][0]["center_mm"][0] += .05
    _, result = run(tmp_path, request)
    assert result["status"] == "failed"
    assert any(c["name"] == "mounting_hole_bore_void" and c["status"] == "fail" for c in result["checks"])


def test_other_cutout_removing_bearing_ring_is_rejected(tmp_path):
    import cadquery as cq
    request, source = fixture(tmp_path)
    pcb, connector = read_step(cq, source).Solids()
    pcb = pcb.cut(cq.Solid.makeCylinder(.5, 3, cq.Vector(8, 5, -.5)))
    cq.exporters.export(cq.Compound.makeCompound([pcb, connector]), str(source))
    _, result = run(tmp_path, request)
    assert result["status"] == "failed"
    check = next(c for c in result["checks"] if c["name"] == "mounting_support_footprint_within_original_pcb")
    assert check["status"] == "fail" and check["measurements"]["missing_pcb_material_mm3"] > .1


def test_nonuniform_pcb_thickness_is_not_approximated(tmp_path):
    import cadquery as cq
    request, source = fixture(tmp_path)
    pcb, connector = read_step(cq, source).Solids()
    pcb = pcb.fuse(cq.Solid.makeBox(3, 3, .4, cq.Vector(20, 5, 1.6)))
    cq.exporters.export(cq.Compound.makeCompound([pcb, connector]), str(source))
    _, result = run(tmp_path, request)
    assert result["status"] == "failed"
    assert any(c["name"] == "original_pcb_constant_section" and c["status"] == "fail" for c in result["checks"])


def test_old_strict_mode_still_rejects_unknown_voids_and_outline(tmp_path):
    request, _ = fixture(tmp_path)
    del request["board"]["metadata"]["pcb_geometry_mode"]
    _, result = run(tmp_path, request)
    assert result["status"] == "unsupported"
    assert result["issues"][-1]["code"] == "UNSUPPORTED_PCB_GEOMETRY"


def test_mount_ligament_can_be_explicit_without_reducing_case_walls(tmp_path):
    from justbuild.cad.request import CadError, validate
    request, _ = fixture(tmp_path)
    m = request["constraints"]["manufacturing"]
    original_wall = m["wall_thickness_mm"]
    m["pcb_mount"].update(post_diameter_mm=6, clearance_diameter_mm=3,
                          minimum_radial_ligament_mm=1.5)
    assert validate(request, tmp_path)["request"]["constraints"]["manufacturing"]["wall_thickness_mm"] == original_wall
    del m["pcb_mount"]["minimum_radial_ligament_mm"]
    with pytest.raises(CadError, match="radial ligament"):
        validate(request, tmp_path)


def test_near_edge_two_mm_holes_use_small_posts_without_moving_holes_or_thinning_case(tmp_path):
    import cadquery as cq
    request, source = fixture(tmp_path)
    connector = read_step(cq, source).Solids()[1]
    pcb = cq.Solid.makeBox(60, 40, 1.6)
    centers = [(2, 2), (58, 2), (2, 38), (58, 38)]
    for h, (x, y) in zip(request["constraints"]["mounting_holes"], centers):
        h.update(center_mm=[x, y, .8], diameter_mm=2)
        pcb = pcb.cut(cq.Solid.makeCylinder(1, 3, cq.Vector(x, y, -.5)))
    cq.exporters.export(cq.Compound.makeCompound([pcb, connector]), str(source))
    m = request["constraints"]["manufacturing"]
    m["pcb_mount"].update(post_diameter_mm=3.8, pilot_diameter_mm=1.2,
                          clearance_diameter_mm=1.8, minimum_radial_ligament_mm=1)
    _, result = run(tmp_path, request)
    assert result["status"] == "ok", result["issues"]
    assert result["metadata"]["generated_configuration"]["wall_thickness_mm"] == 2
    assert [tuple(h["center_mm"][:2]) for h in result["metadata"]["mounting_holes"]] == centers
    assert all(c["status"] == "pass" for c in result["checks"]
               if c["name"] == "mounting_support_footprint_within_original_pcb")


@pytest.mark.parametrize("ligament", [0, -1, True, .001, float("inf")])
def test_invalid_mount_ligament_cannot_relax_constraints(tmp_path, ligament):
    from justbuild.cad.request import CadError, validate
    request, _ = fixture(tmp_path)
    request["constraints"]["manufacturing"]["pcb_mount"]["minimum_radial_ligament_mm"] = ligament
    with pytest.raises(CadError):
        validate(request, tmp_path)


def test_unknown_geometry_mode_is_explicitly_unsupported(tmp_path):
    request, _ = fixture(tmp_path)
    request["board"]["metadata"]["pcb_geometry_mode"] = "bounding_box_approximation"
    _, result = run(tmp_path, request)
    assert result["status"] == "unsupported"
    assert result["artifacts"] == []
