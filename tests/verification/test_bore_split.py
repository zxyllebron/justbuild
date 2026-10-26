"""Analytic split-face coverage: complete circumference is required."""
import pytest

cq = pytest.importorskip("cadquery")

from justbuild.verification.geometry import bore_candidates, box, cylinder


def split_closed(shape):
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed

    operation = ShapeUpgrade_ShapeDivideClosed(shape.wrapped)
    operation.SetNbSplitPoints(1)
    assert operation.Perform()
    return cq.Shape.cast(operation.Result())


def full_board():
    return box([0, 0, 0], [10, 10, 2]).cut(cylinder([5, 5, 0], [0, 0, 1], 2, [-1, 3]))


def test_split_exported_cylinders_pass_independent_pipeline(case):
    case.bore()
    case.shapes["board"] = split_closed(case.shapes["board"])
    case.shapes["base"] = split_closed(case.shapes["base"])
    result = case.run()
    assert result["verdict"] == "pass", result["issues"]
    check = next(c for c in result["checks"] if c["id"] == "mounting:h1:board_datum")
    bore = check["measurements"]["matching_bores"][0]
    assert bore["merged_face_count"] == 2
    assert bore["full_circular_face"] is True
    assert bore["angular_coverage_rad"][0] == pytest.approx([0, 2*3.141592653589793])


def test_duplicate_half_faces_cannot_fake_full_circumference():
    half = full_board().intersect(box([0, 0, 0], [5, 10, 2]))
    repeated = cq.Compound.makeCompound([half, half.copy()])
    bores = bore_candidates(repeated, [5, 5, 0], [0, 0, 1], 1e-5)
    assert bores
    assert not any(b["full_circular_face"] for b in bores)


def test_complementary_halves_at_different_heights_are_not_a_full_bore():
    board = full_board()
    low_left = board.intersect(box([0, 0, 0], [5, 10, 1]))
    high_right = board.intersect(box([5, 0, 1], [10, 10, 2]))
    different_spans = cq.Compound.makeCompound([low_left, high_right])
    bores = bore_candidates(different_spans, [5, 5, 0], [0, 0, 1], 1e-5)
    assert bores
    assert not any(b["full_circular_face"] for b in bores)


def test_coaxial_split_faces_work_with_reversed_reference_axis():
    bores = bore_candidates(split_closed(full_board()), [5, 5, 0], [0, 0, -1], 1e-5)
    assert bores[0]["full_circular_face"]
    assert bores[0]["span_mm"] == pytest.approx([-2, 0])


def test_broadphase_does_not_let_other_solid_blocked_cavity_pass():
    filled = cq.Compound.makeCompound([split_closed(full_board()),
                                      cylinder([5, 5, 0], [0, 0, 1], 1, [-1, 3])])
    # The original cylindrical wall is present, but another component occupies
    # its center. Bbox candidate pruning must retain that exact occupancy test.
    assert not bore_candidates(filled, [5, 5, 0], [0, 0, 1], 1e-5)


def test_non_pcb_component_hole_cannot_impersonate_pcb_datum(case):
    case.bore()
    # The actual PCB has no hole. A different component happens to have a bore
    # on exactly the declared XY axis, at another height in the same assembly.
    pcb = box([2, 2, 2], [8, 8, 3])
    part = box([3, 3, 3.5], [5, 5, 4.5]).cut(cylinder([4, 4, 0], [0, 0, 1], 1, [3, 5]))
    case.shapes["board"] = cq.Compound.makeCompound([pcb, part])
    case.request["board"]["metadata"] = {"pcb_solid_index": 0, "pcb_selection_source": "fixture actual PCB selection"}
    case.request["constraints"]["mounting_holes"][0]["diameter_mm"] = 1
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "mounting:h1:board_datum")
    assert check["status"] == "fail"
    assert check["measurements"]["pcb_solid_index"] == 0
    assert len(check["measurements"]["source_sha256"]) == 64


def test_multisolid_mounting_datum_needs_explicit_pcb_selection(case):
    case.bore()
    case.shapes["board"] = cq.Compound.makeCompound([
        case.shapes["board"], box([6, 6, 2.2], [6.2, 6.2, 2.4])])
    result = case.run()
    check = next(c for c in result["checks"] if c["id"] == "mounting:h1")
    assert check["status"] == "not_checked"
    assert "pcb_solid_index" in check["measurements"]["reason"]
