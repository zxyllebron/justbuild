"""Equivalent execution/caching changes must retain the exact predicates."""
import gc
import weakref

import pytest

cq = pytest.importorskip("cadquery")
from OCP.gp import gp_Pnt
from justbuild.verification import geometry


@pytest.fixture
def solver_calls(monkeypatch):
    calls = {"construct": 0, "load1": 0, "load2": 0, "perform": 0, "success": True}

    class ExactSolver:
        def __init__(self, *args):
            assert not args  # a two-shape constructor would already solve once
            calls["construct"] += 1

        def LoadS1(self, _shape):
            calls["load1"] += 1

        def LoadS2(self, _shape):
            calls["load2"] += 1

        def Perform(self):
            calls["perform"] += 1
            return calls["success"]

        def IsDone(self):
            return calls["success"]

        def NbSolution(self):
            return 1

        def Value(self):
            return 1.0

        def PointOnShape1(self, _index):
            return gp_Pnt(1, 0, 0)

        def PointOnShape2(self, _index):
            return gp_Pnt(2, 0, 0)

    monkeypatch.setattr(geometry, "BRepExtrema_DistShapeShape", ExactSolver)
    return calls


def test_exact_distance_performs_once_and_cached_reports_are_independent(solver_calls):
    a, b = geometry.box([0, 0, 0], [1, 1, 1]), geometry.box([2, 0, 0], [3, 1, 1])
    first = geometry.distance(a, b)
    first["closest_points_mm"][0][0] = 9000
    first["caller_report_field"] = True
    second = geometry.distance(a, b)
    assert second == {"minimum_distance_mm": 1, "closest_points_mm": [[1, 0, 0], [2, 0, 0]]}
    assert solver_calls["construct"] == solver_calls["load1"] == solver_calls["load2"] == solver_calls["perform"] == 1


def test_different_brep_query_is_not_a_cached_distance(solver_calls):
    a = geometry.box([0, 0, 0], [1, 1, 1])
    b, c = geometry.box([2, 0, 0], [3, 1, 1]), geometry.box([5, 0, 0], [6, 1, 1])
    geometry.distance(a, b)
    geometry.distance(a, c)
    assert solver_calls["perform"] == 2


def test_failed_exact_solver_is_not_cached_as_a_pass(solver_calls):
    a, b = geometry.box([0, 0, 0], [1, 1, 1]), geometry.box([2, 0, 0], [3, 1, 1])
    solver_calls["success"] = False
    for _ in range(2):
        with pytest.raises(ValueError, match="minimum-distance operation failed"):
            geometry.distance(a, b)
    assert solver_calls["perform"] == 2


def test_distance_cache_does_not_retain_geometry_wrappers(solver_calls):
    a, b = geometry.box([0, 0, 0], [1, 1, 1]), geometry.box([2, 0, 0], [3, 1, 1])
    reference_a, reference_b = weakref.ref(a), weakref.ref(b)
    geometry.distance(a, b)
    del a, b
    gc.collect()
    assert reference_a() is None and reference_b() is None


def test_changed_real_shape_placement_cannot_reuse_stale_distance():
    a, b = geometry.box([0, 0, 0], [1, 1, 1]), geometry.box([2, 0, 0], [3, 1, 1])
    assert geometry.distance(a, b)["minimum_distance_mm"] == pytest.approx(1)
    b.move(cq.Location(cq.Vector(10, 0, 0)))
    assert geometry.distance(a, b)["minimum_distance_mm"] == pytest.approx(11)


def test_unchanged_solid_preserves_wrapper_and_bounds_across_regions(monkeypatch):
    solid = geometry.box([0, 0, 0], [1, 1, 1])
    original = cq.Solid.BoundingBox
    target_bounds_calls = []

    def counted(current, *args, **kwargs):
        if current is solid:
            target_bounds_calls.append(1)
        return original(current, *args, **kwargs)

    monkeypatch.setattr(cq.Solid, "BoundingBox", counted)
    for x in range(3, 7):
        cutter = geometry.box([x, 0, 0], [x+1, 1, 1])
        remainder = geometry._difference_solids(solid, cutter)
        assert remainder == [solid]
        assert remainder[0] is solid
    assert len(target_bounds_calls) == 1


def test_actual_overlap_cut_is_still_executed():
    solid = geometry.box([0, 0, 0], [2, 2, 2])
    cutter = geometry.box([1, 0, 0], [3, 2, 2])
    result = geometry._difference_solids(solid, cutter)
    assert sum(s.Volume() for s in result) == pytest.approx(4)
    assert all(piece is not solid for piece in result)
