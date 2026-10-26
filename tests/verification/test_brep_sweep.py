"""Real moving BRep sweeps preserve holes without sampling the motion."""
import pytest

cq = pytest.importorskip("cadquery")

from justbuild.verification.geometry import box, cylinder, linear_brep_sweep, intersection_volume


def _lid_with_pin(case):
    hole = cylinder([5, 5, 5], [0, 0, 1], 2, [0, 4])
    pin = cylinder([5, 5, 3], [0, 0, 1], 1, [0, 7])
    case.shapes["lid"] = case.shapes["lid"].cut(hole)
    case.shapes["board"] = cq.Compound.makeCompound([case.shapes["board"], pin])
    for path in case.options["assembly_checks"]:
        path["envelope"] = {"kind": "moving_part_brep", "provenance": "fixture uses complete hash-bound STEP shape"}


def test_lid_actual_hole_keeps_pin_clear_through_entire_path(case):
    _lid_with_pin(case)
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    check = next(c for c in result["checks"] if c["id"] == "assembly:lid-out:board")
    assert check["status"] == "pass"
    assert check["measurements"]["minimum_distance_mm"] == pytest.approx(.5)
    assert check["measurements"]["sweep_evidence"]["sampling"] == "none"
    assert any(c["type"] == "cylinder" for c in check["measurements"]["sweep_evidence"]["zero_volume_parallel_faces"])


def test_mid_path_crossbar_is_blocked_despite_clear_start_and_end(case):
    _lid_with_pin(case)
    bar = box([3, 4.8, 13], [7, 5.2, 13.4])
    case.shapes["board"] = cq.Compound.makeCompound([*case.shapes["board"].Solids(), bar])
    assert intersection_volume(case.shapes["lid"], bar) == 0
    assert intersection_volume(case.shapes["lid"].translate((0, 0, 20)), bar) == 0
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:lid-out:board")
    assert check["status"] == "fail"
    assert check["measurements"]["intersection_volume_mm3"] > 0


def test_transverse_cylinder_is_unsupported_not_assumed_clear(case):
    case.shapes["lid"] = cylinder([0, 0, 7], [1, 0, 0], 1, [0, 10])
    case.options["assembly_checks"][1]["envelope"] = {"kind": "moving_part_brep", "provenance": "fixture"}
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:lid-out")
    assert check["status"] == "not_checked"
    assert "not exactly parallel" in check["measurements"]["reason"]


def test_unsupported_curved_face_fails_closed():
    sphere = cq.Solid.makeSphere(1)
    with pytest.raises(ValueError, match="Unsupported curved sweep"):
        linear_brep_sweep(sphere, [0, 0, 20])


def test_negative_axis_sweep_checks_whole_interval():
    moving = box([0, 0, 5], [2, 2, 6]).cut(cylinder([1, 1, 4], [0, 0, 1], .5, [0, 3]))
    sweep, evidence = linear_brep_sweep(moving, [0, 0, -4])
    assert intersection_volume(sweep, box([0, 0, 2], [.4, .4, 3])) > 0
    assert intersection_volume(sweep, cylinder([1, 1, 0], [0, 0, 1], .25, [0, 8])) == 0
    assert evidence["source_solid_count"] == 1


def test_sweep_keeps_multiple_disconnected_solids():
    moving = cq.Compound.makeCompound([box([0, 0, 0], [1, 1, 1]), box([3, 0, 0], [4, 1, 1])])
    sweep, evidence = linear_brep_sweep(moving, [0, 0, 10])
    assert evidence["source_solid_count"] == 2
    assert intersection_volume(sweep, box([3.2, .2, 5], [3.8, .8, 6])) > 0
    assert intersection_volume(sweep, box([1.5, .2, 5], [2.5, .8, 6])) == 0


def test_brep_path_still_requires_full_obstacle_inventory(case):
    _lid_with_pin(case)
    case.options["assembly_checks"][1]["against"] = ["base"]
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:lid-out")
    assert check["status"] == "not_checked"
    assert "every other assembly part" in check["measurements"]["reason"]


def test_brep_touch_still_needs_explicit_region(case):
    case.shapes["lid"] = box([0, 0, 6], [10, 10, 7])
    case.options["assembly_checks"][1]["envelope"] = {"kind": "moving_part_brep", "provenance": "fixture"}
    case.options["intentional_contacts"] = [{"parts": ["base", "lid"], "mode": "touch_only", "reason": "fixture rim contact",
        "provenance": "fixture", "regions": [{"kind": "box", "min_mm": [-.01, -.01, 5.99],
        "max_mm": [10.01, 10.01, 6.01], "provenance": "fixture rim plane"}]}]
    assert case.run()["verdict"] == "pass"
    del case.options["intentional_contacts"]
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:lid-out:base")
    assert check["status"] == "fail"
    assert check["measurements"]["path_contact_policy"] == "contact_has_no_unique_touch_only_declaration"
