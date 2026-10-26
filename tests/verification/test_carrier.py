"""Known removable-carrier/screen roles must join every relevant core check."""
import json

from justbuild.verification.geometry import box, cylinder


def _add_carrier(case):
    case.request["constraints"]["display"] = {"support_structure": "removable_pcb_carrier"}
    case.shapes["screen"] = box([2, 2, 1.1], [8, 8, 1.5])
    case.shapes["pcb_carrier"] = box([2, 2, 1.6], [8, 8, 1.8])
    paths = case.options["assembly_checks"]
    paths[0]["against"] = ["base", "pcb_carrier", "screen"]
    paths[0]["minimum_clearance_mm"] = .1
    paths[1]["against"] = ["base", "board", "pcb_carrier", "screen"]
    for part, against, absent in (("screen", ["base"], ["board", "lid", "pcb_carrier"]),
                                  ("pcb_carrier", ["base", "screen"], ["board", "lid"])):
        paths.append({"id": part + "-out", "moving_part": part,
            "envelope": {"kind": "moving_part_brep", "provenance": "fixture complete real part"},
            "translation_mm": [0, 0, 20], "against": against, "absent_parts": absent,
            "stage_provenance": "screen is installed before removable carrier, board and lid",
            "minimum_clearance_mm": 0, "provenance": "fixture verified assembly sequence"})


def _run(case, mutate=None):
    case.write()
    for a in case.cad["artifacts"]:
        if a["kind"] == "screen_step":
            a["kind"] = "screen_envelope_step"
    if mutate:
        mutate(case.cad)
    case.cad_path.write_text(json.dumps(case.cad))
    return case.verify_written()


def test_all_five_parts_loaded_and_all_ten_collision_pairs_checked(case):
    _add_carrier(case)
    result = _run(case)
    assert result["verdict"] == "pass", result["issues"]
    assert {g["part_id"] for g in result["checked_geometry"]} == {"board", "base", "lid", "screen", "pcb_carrier"}
    assert len([c for c in result["checks"] if c["id"].startswith("collision:")]) == 10
    assert any(c["id"] == "assembly:screen-out:base" and c["status"] == "pass" for c in result["checks"])
    assert any(c["id"] == "assembly:pcb_carrier-out:screen" and c["status"] == "pass" for c in result["checks"])


def test_carrier_board_collision_is_not_omitted(case):
    _add_carrier(case)
    case.shapes["pcb_carrier"] = box([2, 2, 2.5], [8, 8, 3.5])
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "collision:board:pcb_carrier")
    assert check["status"] == "fail"
    assert check["measurements"]["intersection_volume_mm3"] > 0


def test_carrier_artifact_without_explicit_structure_is_incomplete(case):
    _add_carrier(case)
    del case.request["constraints"]["display"]
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "undeclared:pcb_carrier")
    assert check["status"] == "not_checked"


def test_missing_carrier_artifact_does_not_verify_other_three_only(case):
    _add_carrier(case)
    result = _run(case, lambda cad: cad.update(artifacts=[a for a in cad["artifacts"] if a["kind"] != "pcb_carrier_step"]))
    assert result["verdict"] == "incomplete"
    check = next(c for c in result["checks"] if c["id"] == "solid:pcb_carrier")
    assert check["status"] == "not_checked"


def test_missing_screen_path_blocks_release(case):
    _add_carrier(case)
    case.options["assembly_checks"] = [p for p in case.options["assembly_checks"] if p["moving_part"] != "screen"]
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "assembly:missing:screen")
    assert check["status"] == "not_checked"
    assert result["verdict"] == "incomplete"


def test_screen_path_catches_midpoint_base_obstacle(case):
    _add_carrier(case)
    case.shapes["base"] = case.shapes["base"].fuse(box([1, 4, 4], [9, 5, 4.2]))
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "assembly:screen-out:base")
    assert check["status"] == "fail"


def test_declared_stage_cannot_silently_omit_screen_from_carrier_path(case):
    _add_carrier(case)
    path = next(p for p in case.options["assembly_checks"] if p["moving_part"] == "pcb_carrier")
    path["against"] = ["base"]
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "assembly:pcb_carrier-out")
    assert check["status"] == "not_checked"
    assert "every other assembly part" in check["measurements"]["reason"]


def test_original_pcb_bore_can_target_removable_carrier(case):
    case.bore()
    _add_carrier(case)
    case.shapes["pcb_carrier"] = case.shapes["pcb_carrier"].cut(cylinder([4, 4, 0], [0, 0, 1], 2, [1.5, 1.9]))
    target = case.options["mounting_checks"]["h1"][0]
    target.update(part_id="pcb_carrier", span_mm=[1.6, 1.8])
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "mounting:h1:0")
    assert check["status"] == "pass", result["issues"]
    assert check["measurements"]["part_id"] == "pcb_carrier"


def test_opening_plan_must_include_carrier_and_screen(case):
    case.port()
    _add_carrier(case)
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "port:usb")
    assert check["status"] == "not_checked"
    assert "all declared shell and screen parts" in check["measurements"]["reason"]


def test_individually_empty_accessory_stages_cannot_fake_assembly_sequence(case):
    _add_carrier(case)
    path = next(p for p in case.options["assembly_checks"] if p["moving_part"] == "pcb_carrier")
    path["against"] = ["base"]
    path["absent_parts"] = ["board", "lid", "screen"]
    result = _run(case)
    check = next(c for c in result["checks"] if c["id"] == "assembly:pcb_carrier-out")
    assert check["status"] == "not_checked"
    assert "assembly order" in check["measurements"]["reason"]
