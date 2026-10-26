import builtins
import json

import pytest

from justbuild.verification import verify
from justbuild.verification.engine import Verifier


def checks(result, category):
    return [c for c in result["checks"] if c["category"] == category]


def test_valid_solids_real_clearance_and_provenance(case):
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    assert result["status"] == "ok"
    assert len(result["checked_geometry"]) == 3
    measured = next(c for c in checks(result, "clearances") if c["id"] == "clearance:0")
    assert measured["measurements"]["minimum_distance_mm"] == pytest.approx(1.0)
    # The shell bbox contains the PCB bbox. B-rep geometry still proves separation.
    assert all(c["status"] == "pass" for c in checks(result, "collision"))
    assert all(c["status"] == "not_checked" for c in checks(result, "thermal"))
    assert result["repair_proposals"] == []


def test_intersecting_board_and_shell_report_volume_and_repair(case):
    from justbuild.verification.geometry import box
    case.shapes["board"] = box([0.5, 2, 2], [8, 8, 3])
    result = case.run()
    assert result["verdict"] == "fail"
    hit = next(c for c in checks(result, "collision") if c["status"] == "fail")
    assert hit["measurements"]["intersection_volume_mm3"] == pytest.approx(3)
    assert result["repair_proposals"][0]["auto_apply"] is False
    assert "PCB mounting-hole centers" in result["repair_proposals"][0]["preserve"]


def test_clearance_threshold_is_independent_of_collision(case):
    case.options["clearances"][0]["minimum_mm"] = 1.2
    result = case.run()
    assert result["verdict"] == "fail"
    assert all(c["status"] == "pass" for c in checks(result, "collision"))
    hit = next(c for c in result["checks"] if c["id"] == "clearance:0")
    assert hit["measurements"]["deficit_mm"] == pytest.approx(0.2)


def test_numerical_clearance_tolerance_is_reported(case):
    case.options["clearances"][0]["minimum_mm"] = 1.000005
    result = case.run()
    assert result["verdict"] == "pass"
    assert result["numerical_policy"]["tolerance_mm"] == 1e-5


@pytest.mark.parametrize("declared,overlap,expected", [(False, False, "fail"),
                                                        (True, False, "pass"),
                                                        (True, True, "fail")])
def test_touch_only_contacts_never_exempt_material_overlap(case, declared, overlap, expected):
    case.shapes["lid"] = case.shapes["lid"].translate((0, 0, -0.3 if overlap else -0.2))
    case.options["assembly_checks"][1]["envelope"]["min_mm"][2] -= 0.3 if overlap else 0.2
    case.options["assembly_checks"][1]["envelope"]["max_mm"][2] -= 0.3 if overlap else 0.2
    if declared:
        case.options["intentional_contacts"] = [{"parts": ["base", "lid"], "mode": "touch_only",
                                                  "reason": "lid mating seam", "provenance": "fixture",
                                                  "regions": [{"kind": "box", "min_mm": [-0.1, -0.1, 5.8],
                                                               "max_mm": [10.1, 10.1, 6.1],
                                                               "provenance": "seam band with buffer"}]}]
    result = case.run()
    assert result["verdict"] == expected, result["issues"]


def test_contact_does_not_override_an_explicit_clearance(case):
    case.shapes["lid"] = case.shapes["lid"].translate((0, 0, -0.2))
    case.options["intentional_contacts"] = [{"parts": ["base", "lid"], "mode": "touch_only",
                                              "reason": "seam", "provenance": "fixture",
                                              "regions": [{"kind": "box", "min_mm": [-0.1, -0.1, 5.9],
                                                           "max_mm": [10.1, 10.1, 6.1],
                                                           "provenance": "confirmed seam"}]}]
    case.options["clearances"].append({"parts": ["base", "lid"], "minimum_mm": 0.2,
                                        "provenance": "conflicting explicit requirement"})
    result = case.run()
    assert result["verdict"] == "fail"
    assert any(i["code"] == "CLEARANCE_TOO_SMALL" for i in result["issues"])


@pytest.mark.parametrize("dx,diameter,span,max_diameter,expected", [
    (0, 2, (0, 1), None, "pass"), (0.2, 3, (0, 1), None, "fail"),
    (0, 1.6, (0, 1), None, "fail"), (0, 2, (0, 0.7), None, "fail"),
    (0, 3, (0, 1), 2.5, "fail"), (0.02, 2.2, (0, 1), 2.5, "pass"),
])
def test_real_mounting_bore_axis_diameter_and_blind_hole(case, dx, diameter, span, max_diameter, expected):
    case.bore(dx, diameter, span, max_diameter)
    result = case.run()
    assert result["verdict"] == expected, result["issues"]
    measured = next(c for c in result["checks"] if c["id"] == "mounting:h1:0")["measurements"]
    assert measured["candidate_bores"]
    assert measured["candidate_bores"][0]["axis_offset_mm"] == pytest.approx(abs(dx), abs=1e-6)


def test_hole_probe_in_empty_space_cannot_pass_as_a_mounting_hole(case):
    case.bore()
    case.request["constraints"]["mounting_holes"][0]["center_mm"] = [30, 30, 0]
    result = case.run()
    assert result["verdict"] == "fail"
    hit = next(c for c in result["checks"] if c["id"] == "mounting:h1:0")
    assert hit["measurements"]["blocking_volume_mm3"] == 0
    assert hit["measurements"]["matching_bores"] == []


@pytest.mark.parametrize("opened,expected", [(True, "pass"), (False, "fail")])
def test_required_port_opening_and_continuous_plug_envelope(case, opened, expected):
    case.port(opened)
    result = case.run()
    assert result["verdict"] == expected, result["issues"]
    assert any(c["id"] == "insertion:usb:base" for c in result["checks"])


def test_path_detects_obstacle_between_clear_endpoints(case):
    from justbuild.verification.geometry import box
    case.shapes["lid"] = box([15, 4, 3], [16, 6, 4])
    case.options["assembly_checks"].append({
        "id": "lid-slide", "moving_part": "lid",
        "envelope": {"kind": "box", "min_mm": [15, 4, 3], "max_mm": [16, 6, 4],
                     "provenance": "fixture solid envelope"},
        "translation_mm": [-20, 0, 0], "against": ["base", "board"],
        "minimum_clearance_mm": 0, "provenance": "fixture slide"})
    assert box([-5, 4, 3], [-4, 6, 4]).intersect(case.shapes["base"]).Volume() == 0
    result = case.run()
    assert result["verdict"] == "fail"
    hit = next(c for c in result["checks"] if c["id"] == "assembly:lid-slide:base")
    assert hit["measurements"]["intersection_volume_mm3"] > 0
    assert hit["measurements"]["sweep_method"].startswith("exact_continuous")


def test_clear_linear_lid_removal_contains_actual_part(case):
    case.options["assembly_checks"].append({
        "id": "lid-up", "moving_part": "lid",
        "envelope": {"kind": "box", "min_mm": [0, 0, 6.2], "max_mm": [10, 10, 7.2],
                     "provenance": "fixture lid envelope"},
        "translation_mm": [0, 0, 20], "against": ["board", "base"],
        "minimum_clearance_mm": 0.1, "provenance": "fixture lid removal"})
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    assert next(c for c in result["checks"] if c["id"].endswith(":containment"))["status"] == "pass"


def test_declared_assembly_box_cannot_omit_moving_solid(case):
    case.options["assembly_checks"].append({
        "id": "bad-envelope", "moving_part": "lid",
        "envelope": {"kind": "box", "min_mm": [20, 0, 6.2], "max_mm": [21, 1, 7.2],
                     "provenance": "incorrect fixture envelope"},
        "translation_mm": [0, 0, 20], "against": ["board", "base"],
        "minimum_clearance_mm": 0.1, "provenance": "fixture"})
    result = case.run()
    assert result["verdict"] == "fail"
    assert any(i["code"] == "MOVING_PART_OUTSIDE_ENVELOPE" for i in result["issues"])


@pytest.mark.parametrize("blocked,expected", [(False, "pass"), (True, "fail")])
def test_service_keepout_solid(case, blocked, expected):
    case.request["constraints"]["keepouts"] = [{
        "id": "tool", "purpose": "screwdriver access", "provenance": "fixture requirement",
        "geometry": {"kind": "box", "min_mm": [0.5 if blocked else 2, 4, 4],
                     "max_mm": [3, 5, 5], "provenance": "fixture tool envelope"},
        "against": ["base", "lid"], "minimum_clearance_mm": 0}]
    result = case.run()
    assert result["verdict"] == expected, result["issues"]


@pytest.mark.parametrize("missing", ["coverage", "geometry_frame", "mounting_checks", "port_checks"])
def test_missing_mechanical_information_is_incomplete(case, missing):
    if missing == "mounting_checks":
        case.bore()
    if missing == "port_checks":
        case.port()
    case.options.pop(missing, None)
    result = case.run()
    assert result["verdict"] == "incomplete", result["issues"]
    assert result["status"] == "needs_input"


def test_unknown_thermal_analysis_can_be_required_and_blocks_release(case):
    case.options["required_checks"] = ["thermal"]
    result = case.run()
    assert result["verdict"] == "incomplete"
    assert checks(result, "thermal")[0]["required"] is True


def test_non_axis_aligned_path_remains_not_checked(case):
    case.port()
    case.request["constraints"]["ports"][0]["insertion_direction"] = [2 ** -0.5, 2 ** -0.5, 0]
    case.options["port_checks"]["usb"]["insertion"]["translation_mm"] = [-5, -5, 0]
    result = case.run()
    assert result["verdict"] == "incomplete"


@pytest.mark.parametrize("change", ["hash", "missing_file", "job", "units", "duplicate_artifact"])
def test_artifact_contract_fails_closed(case, change):
    case.write()
    if change == "hash":
        case.cad["artifacts"][0]["sha256"] = "0" * 64
    elif change == "missing_file":
        (case.root / "base.step").unlink()
    elif change == "job":
        case.cad["job_id"] = "wrong-job"
    elif change == "units":
        case.cad["artifacts"][0]["units"] = "inch"
    else:
        case.cad["artifacts"].append(case.cad["artifacts"][0])
    case.cad_path.write_text(json.dumps(case.cad))
    result = case.verify_written()
    assert result["verdict"] != "pass"
    if change == "hash":
        assert result["verdict"] == "fail"


def test_dangling_surface_beside_solid_is_invalid(case):
    import cadquery as cq
    surface = cq.Face.makePlane(1, 1, (20, 20, 20))
    case.shapes["lid"] = cq.Compound.makeCompound([case.shapes["lid"], surface])
    result = case.run()
    assert result["verdict"] == "fail"
    assert any(i["code"] == "INVALID_SOLID" for i in result["issues"])


def test_nonrigid_board_transform_is_rejected(case):
    case.options["board_transform"] = [[2, 0, 0, 0], [0, 1, 0, 0],
                                          [0, 0, 1, 0], [0, 0, 0, 1]]
    result = case.run()
    assert result["verdict"] == "incomplete"


def test_board_rigid_placement_is_applied_to_real_geometry(case):
    case.shapes["board"] = case.shapes["board"].translate((-20, 0, 0))
    case.options["board_transform"] = [[1, 0, 0, 20], [0, 1, 0, 0],
                                          [0, 0, 1, 0], [0, 0, 0, 1]]
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    board = next(g for g in result["checked_geometry"] if g["part_id"] == "board")
    assert board["source_to_board_transform"][0][3] == 20


def test_artifact_changed_during_check_is_detected(case, monkeypatch):
    original = Verifier.collisions

    def mutate(self):
        original(self)
        with (case.root / "base.step").open("a") as stream:
            stream.write("\n")

    monkeypatch.setattr(Verifier, "collisions", mutate)
    result = case.run()
    assert result["verdict"] == "fail"
    assert any(i["code"] == "INPUT_CHANGED" for i in result["issues"])


def test_missing_optional_backend_returns_unsupported(case, monkeypatch):
    original = builtins.__import__

    def no_backend(name, globals=None, locals=None, fromlist=(), level=0):
        if level and "geometry" in fromlist:
            raise ImportError("synthetic missing geometry dependency")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_backend)
    result = case.run()
    assert result["status"] == "unsupported"
    assert result["verdict"] == "incomplete"


def test_contact_exception_cannot_hide_another_contact_location(case):
    case.shapes["lid"] = case.shapes["lid"].translate((0, 0, -0.2))
    case.options["intentional_contacts"] = [{
        "parts": ["base", "lid"], "mode": "touch_only", "reason": "small approved seam region",
        "provenance": "fixture", "regions": [{"kind": "box", "min_mm": [-0.1, -0.1, 5.9],
                                                "max_mm": [1.1, 1.1, 6.1], "provenance": "fixture"}]}]
    result = case.run()
    assert result["verdict"] == "fail"
    assert any(i["code"] == "UNDECLARED_CONTACT_LOCATION" for i in result["issues"])


def test_confirmed_metadata_hole_must_exist_in_actual_pcb(case):
    from justbuild.verification.geometry import box
    case.bore()
    case.shapes["board"] = box([2, 2, 2], [8, 8, 3])
    result = case.run()
    assert result["verdict"] == "fail"
    assert any(i["code"] == "PCB_MOUNTING_DATUM_MISMATCH" for i in result["issues"])


def test_empty_assembly_inventory_cannot_approve_screw_lid_structure(case):
    case.options.pop("assembly_checks")
    result = case.run()
    assert result["verdict"] == "incomplete"
    assert any(i["code"] == "ASSEMBLY_PATH_MISSING" for i in result["issues"])


def test_assembly_obstacle_cannot_be_silently_omitted(case):
    case.options["assembly_checks"][0].pop("absent_parts")
    result = case.run()
    assert result["verdict"] == "incomplete"


def test_insertion_must_check_board_components_as_well_as_shell(case):
    case.port()
    case.options["port_checks"]["usb"]["insertion"]["against"].remove("board")
    result = case.run()
    assert result["verdict"] == "incomplete"


def test_kernel_error_cannot_pass(case, monkeypatch):
    from justbuild.verification import geometry

    def broken(*args):
        raise RuntimeError("synthetic OCC boolean failure")

    monkeypatch.setattr(geometry, "intersection_volume", broken)
    result = case.run()
    assert result["verdict"] == "incomplete"
    assert any(i["code"] == "CHECK_UNAVAILABLE" for i in result["issues"])


def test_internal_connector_still_needs_an_insertion_path(case):
    case.request["constraints"]["ports"] = [{"id": "internal", "access_mode": "internal"}]
    result = case.run()
    assert result["verdict"] == "incomplete"


def test_split_pcb_bore_is_explicitly_unsupported(case):
    from justbuild.verification.geometry import box, cylinder
    case.bore()
    # This opening genuinely lacks an angular sector, rather than being a full
    # hole whose cylindrical face was merely split by the STEP exporter.
    case.shapes["board"] = box([3.5, 2, 2], [8, 8, 3]).cut(cylinder([4, 4, 0], [0, 0, 1], 2, [1, 4]))
    result = case.run()
    assert result["verdict"] == "incomplete", result["issues"]
    assert any(i["code"] == "UNSUPPORTED_BORE_TOPOLOGY" for i in result["issues"])


def test_malformed_required_analysis_entry_cannot_be_silently_ignored(case):
    case.options["required_checks"] = [{"thermal": True}]
    result = case.run()
    assert result["verdict"] == "incomplete"
