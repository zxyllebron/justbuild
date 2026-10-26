"""Continuous multi-box envelopes preserve real underside geometry and gaps."""
import pytest

cq = pytest.importorskip("cadquery")

from justbuild.verification.geometry import box


def boxes_envelope(*bounds):
    return {"kind": "boxes", "provenance": "fixture complete moving-solid inventory",
            "boxes": [{"kind": "box", "min_mm": low, "max_mm": high,
                       "provenance": "fixture exact solid envelope"} for low, high in bounds]}


def test_missing_underside_pin_box_fails_actual_solid_coverage(case):
    pin = box([3, 3, 1.5], [3.2, 3.2, 2])
    case.shapes["board"] = cq.Compound.makeCompound([case.shapes["board"], pin])
    case.options["assembly_checks"][0]["envelope"] = boxes_envelope(([2, 2, 2], [8, 8, 3]))
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:board-out:containment")
    assert check["status"] == "fail"
    assert check["measurements"]["solid_count"] == 2
    assert check["measurements"]["solid_coverage"][1]["containing_box_indices"] == []


def test_underside_pin_is_preserved_with_its_own_box(case):
    pin = box([3, 3, 1.5], [3.2, 3.2, 2])
    case.shapes["board"] = cq.Compound.makeCompound([case.shapes["board"], pin])
    case.options["assembly_checks"][0]["envelope"] = boxes_envelope(
        ([2, 2, 2], [8, 8, 3]), ([3, 3, 1.5], [3.2, 3.2, 2]))
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]


def test_individual_boxes_leave_real_gap_around_obstacle(case):
    case.shapes["board"] = cq.Compound.makeCompound([
        box([2, 2, 3], [4, 8, 4]), box([6, 2, 3], [8, 8, 4])])
    case.shapes["base"] = case.shapes["base"].fuse(box([4.8, 4, 1], [5.2, 6, 4]))
    path = case.options["assembly_checks"][0]
    path["envelope"] = boxes_envelope(([2, 2, 3], [4, 8, 4]), ([6, 2, 3], [8, 8, 4]))
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    # An overall bbox incorrectly fills the real central gap and is blocked.
    path["envelope"] = {"kind": "box", "min_mm": [2, 2, 3], "max_mm": [8, 8, 4],
                        "provenance": "fixture conservative overall box"}
    blocked = case.run()
    assert blocked["verdict"] == "fail"
    assert any(c["id"] == "assembly:board-out:base" and c["status"] == "fail" for c in blocked["checks"])


def test_continuous_box_sweep_catches_obstacle_between_clear_endpoints(case):
    path = case.options["assembly_checks"][0]
    path["envelope"] = boxes_envelope(([2, 2, 2], [8, 8, 3]))
    path["translation_mm"] = [20, 0, 0]
    assert box([22, 2, 2], [28, 8, 3]).intersect(case.shapes["base"]).Volume() == 0
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:board-out:box:0:base")
    assert check["status"] == "fail"
    assert check["measurements"]["intersection_volume_mm3"] > 0


def test_disjoint_boxes_total_bounds_cannot_hide_missing_solid(case):
    case.options["assembly_checks"][0]["envelope"] = boxes_envelope(
        ([1, 1, 1.5], [3, 9, 3.5]), ([7, 1, 1.5], [9, 9, 3.5]))
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "assembly:board-out:containment")
    assert check["status"] == "fail"


def test_boxes_touch_uses_same_spatial_contact_policy(case):
    case.shapes["base"] = case.shapes["base"].fuse(box([3, 3, 1], [4, 4, 2]))
    case.options["clearances"] = []
    path = case.options["assembly_checks"][0]
    path["envelope"] = boxes_envelope(([2, 2, 2], [8, 8, 3]))
    path["minimum_clearance_mm"] = 0
    case.options["intentional_contacts"] = [{
        "parts": ["board", "base"], "mode": "touch_only", "reason": "fixture support top",
        "provenance": "fixture bounded touch inventory",
        "regions": [{"kind": "box", "min_mm": [2.9, 2.9, 1.9], "max_mm": [4.1, 4.1, 2.1],
                     "provenance": "fixture support-top region"}]}]
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    check = next(c for c in result["checks"] if c["id"] == "assembly:board-out:box:0:base")
    assert check["measurements"]["path_contact_policy"] == "touch_must_stay_within_declared_contact_regions"

