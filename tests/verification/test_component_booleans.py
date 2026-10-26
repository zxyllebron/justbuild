"""Conservative per-solid Commons never convert a failed Null into a pass."""
import pytest

cq = pytest.importorskip("cadquery")
from justbuild.verification import geometry


def test_overlapping_components_report_sum_upper_bound_not_union_volume():
    source = cq.Compound.makeCompound([geometry.box([0, 0, 0], [2, 2, 2]),
                                      geometry.box([1, 0, 0], [3, 2, 2])])
    evidence = {}
    volume = geometry.intersection_volume(source, geometry.box([-1, -1, -1], [4, 3, 3]), details=evidence)
    assert volume == pytest.approx(16)  # the geometric union has volume 12
    assert evidence["volume_semantics"] == "sum_of_per_solid_intersections_upper_bound_on_union_volume"
    assert evidence["exact_common_pair_count"] == 2


def test_disjoint_conservative_bounds_skip_exact_common_only_when_safe(monkeypatch):
    def unexpected(*_args):
        raise AssertionError("No exact Common should be needed for disjoint bounds")

    monkeypatch.setattr(geometry, "BRepAlgoAPI_Common", unexpected)
    evidence = {}
    volume = geometry.intersection_volume(geometry.box([0, 0, 0], [1, 1, 1]),
                                          geometry.box([2, 2, 2], [3, 3, 3]), details=evidence)
    assert volume == 0
    assert evidence["aabb_disjoint_pair_count"] == 1
    assert evidence["exact_common_pair_count"] == 0


def test_overlapping_bounds_still_need_real_geometry_common():
    shell = geometry.box([0, 0, 0], [10, 10, 10]).cut(geometry.box([1, 1, 1], [9, 9, 11]))
    evidence = {}
    assert geometry.intersection_volume(shell, geometry.box([2, 2, 2], [3, 3, 3]), details=evidence) == 0
    assert evidence["aabb_disjoint_pair_count"] == 0
    assert evidence["exact_common_pair_count"] == 1


@pytest.mark.parametrize("operation", ["Common", "Cut"])
def test_null_boolean_result_is_never_zero_volume(operation, monkeypatch):
    from OCP.TopoDS import TopoDS_Shape

    class NullResult:
        def __init__(self, *_args):
            pass

        def IsDone(self):
            return True

        def __getattr__(self, name):
            if name in {"SetArguments", "SetTools", "SetNonDestructive", "SetRunParallel", "Build"}:
                return lambda *_args: None
            raise AttributeError(name)

        def Shape(self):
            return TopoDS_Shape()

    monkeypatch.setattr(geometry, "BRepAlgoAPI_"+operation, NullResult)
    a, b = geometry.box([0, 0, 0], [2, 2, 2]), geometry.box([1, 1, 1], [3, 3, 3])
    with pytest.raises(ValueError, match="Null"):
        (geometry.intersection_volume if operation == "Common" else geometry.difference_volume)(a, b)
