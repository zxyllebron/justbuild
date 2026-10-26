"""Strict BRep-bound separation can avoid exact distances without false passes."""
from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")

from justbuild.verification import geometry as g
from justbuild.verification.engine import Verifier


@pytest.mark.parametrize("shape", [
    lambda: g.box([4, 0, 0], [5, 1, 1]),
    lambda: g.box([4, 5, 6], [5, 6, 7]),
    lambda: cq.Solid.makeSphere(1, cq.Vector(5, 2, 0)),
    lambda: g.cylinder([5, 2, -2], [0, 0, 1], 2, [0, 4]),
])
def test_conservative_bound_never_exceeds_real_brep_distance(shape):
    moving = g.box([0, 0, 0], [1, 1, 1])
    obstacle = shape()
    bound = g.separation_lower_bound(moving, obstacle)
    exact = g.distance(moving, obstacle)["minimum_distance_mm"]
    assert 0 <= bound["minimum_distance_lower_bound_mm"] <= exact + 1e-12
    assert "minimum_distance_mm" not in bound
    assert "closest_points_mm" not in bound


def _check(moving, obstacle, margin, *, contact=False):
    runner = Verifier(Path("not-read-request.json"), Path("not-read-cad.json"))
    runner.kernel = g
    runner.tol = .001
    runner.vtol = 1e-7
    runner.parts = {"board": moving, "base": obstacle}
    runner.options = {}
    entry = {"moving_part": "board", "against": ["base"], "minimum_clearance_mm": margin}
    runner.empty_envelope("bound-fixture", "assembly_paths", "fixture", entry, moving,
                          enforce_contact_policy=contact)
    return runner.result["checks"][0]


def test_strict_far_separation_reports_only_lower_bound_and_skips_exact(monkeypatch):
    moving, obstacle = g.box([0, 0, 0], [1, 1, 1]), g.box([4, 0, 0], [5, 1, 1])
    def forbidden(*args):
        raise AssertionError("Exact solver is unnecessary for the strictly separated bounds")
    monkeypatch.setattr(g, "distance", forbidden)
    check = _check(moving, obstacle, 2.0, contact=True)
    assert check["status"] == "pass"
    measured = check["measurements"]
    assert measured["minimum_distance_lower_bound_mm"] > 2.0
    assert measured["minimum_distance_lower_bound_mm"] <= 3.0
    assert measured["exact_distance_solver_skipped"] is True
    assert "minimum_distance_mm" not in measured
    assert "closest_points_mm" not in measured


def test_near_contact_uses_exact_distance_and_contact_policy(monkeypatch):
    moving = g.box([0, 0, 0], [1, 1, 1])
    obstacle = g.box([1.0009, 0, 0], [2, 1, 1])
    original, calls = g.distance, []
    def counting(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(g, "distance", counting)
    check = _check(moving, obstacle, 0, contact=True)
    assert calls
    assert check["status"] == "fail"
    assert check["measurements"]["minimum_distance_mm"] == pytest.approx(.0009)
    assert check["measurements"]["path_contact_policy"] == "contact_has_no_unique_touch_only_declaration"
    assert "exact_distance_solver_skipped" not in check["measurements"]


def test_bbox_overlap_inside_hole_still_runs_exact_solver(monkeypatch):
    ring = g.box([0, 0, 0], [10, 10, 1]).cut(g.box([2, 2, -1], [8, 8, 2]))
    moving = g.box([4, 4, .2], [6, 6, .8])
    original, calls = g.distance, []
    def counting(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(g, "distance", counting)
    check = _check(moving, ring, 1.0)
    assert calls
    assert check["status"] == "pass"
    assert check["measurements"]["minimum_distance_mm"] == pytest.approx(2.0)


def test_bound_equal_to_requested_clearance_is_not_a_strict_proof(monkeypatch):
    moving, obstacle = g.box([0, 0, 0], [1, 1, 1]), g.box([4, 0, 0], [5, 1, 1])
    actual_bound = g.separation_lower_bound(moving, obstacle)["minimum_distance_lower_bound_mm"]
    original, calls = g.distance, []
    def counting(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(g, "distance", counting)
    check = _check(moving, obstacle, actual_bound)
    assert calls
    assert check["measurements"]["minimum_distance_mm"] == pytest.approx(3.0)


def test_insufficient_clearance_still_fails_exact(monkeypatch):
    moving, obstacle = g.box([0, 0, 0], [1, 1, 1]), g.box([2, 0, 0], [3, 1, 1])
    check = _check(moving, obstacle, 2.0)
    assert check["status"] == "fail"
    assert check["measurements"]["minimum_distance_mm"] == pytest.approx(1.0)


def test_positive_penetration_cannot_short_circuit_even_with_bad_bound(monkeypatch):
    moving, obstacle = g.box([0, 0, 0], [2, 2, 2]), g.box([1, 1, 1], [3, 3, 3])
    monkeypatch.setattr(g, "separation_lower_bound", lambda *args: {"minimum_distance_lower_bound_mm": 100})
    check = _check(moving, obstacle, 0)
    assert check["status"] == "fail"
    assert check["measurements"]["intersection_volume_mm3"] > 0
    assert "minimum_distance_mm" in check["measurements"]


def test_moving_a_cached_wrapper_cannot_reuse_old_separation():
    moving, obstacle = g.box([0, 0, 0], [1, 1, 1]), g.box([4, 0, 0], [5, 1, 1])
    assert g.separation_lower_bound(moving, obstacle)["minimum_distance_lower_bound_mm"] > 2.9
    obstacle.wrapped.Move(cq.Location((-4, 0, 0)).wrapped)
    assert g.separation_lower_bound(moving, obstacle)["minimum_distance_lower_bound_mm"] == 0


def test_bound_record_copies_cannot_change_cached_evidence():
    a, b = g.box([0, 0, 0], [1, 1, 1]), g.box([4, 0, 0], [5, 1, 1])
    first = g.separation_lower_bound(a, b)
    first["conservative_bounds_mm"][0]["max_mm"][0] = 99
    second = g.separation_lower_bound(a, b)
    assert second["minimum_distance_lower_bound_mm"] > 2.9
    assert second["conservative_bounds_mm"][0]["max_mm"][0] < 1.001
