"""Small CadQuery/OCC adapter. STEP B-reps, never meshes, are authoritative."""

from __future__ import annotations

import math
from pathlib import Path
import weakref

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepTools import BRepTools
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopoDS import TopoDS_Iterator
from OCP.TopTools import TopTools_ListOfShape


_CAVITY_CACHE = weakref.WeakKeyDictionary()
_SOLID_BOUNDS_CACHE = weakref.WeakKeyDictionary()
_DISTANCE_CACHE = weakref.WeakKeyDictionary()


def solid_bounds(shape):
    """Independent OCC bounds for all actual solids, cached on this B-rep."""
    if shape not in _SOLID_BOUNDS_CACHE:
        records = []
        solids = [shape] if shape.ShapeType() == "Solid" else shape.Solids()
        for index, solid in enumerate(solids):
            bounds = solid.BoundingBox()
            records.append({"solid_index": index,
                            "min_mm": [bounds.xmin, bounds.ymin, bounds.zmin],
                            "max_mm": [bounds.xmax, bounds.ymax, bounds.zmax]})
        _SOLID_BOUNDS_CACHE[shape] = records
    return _SOLID_BOUNDS_CACHE[shape]


def boxes_contain_solids(shape, boxes):
    """A union bbox is insufficient: each real solid needs one containing box.

    Bounds are a conservative containment proof, never a collision or path
    proof. No numerical expansion of declared boxes is permitted here.
    """
    coverage = []
    for solid in solid_bounds(shape):
        matches = [index for index, (low, high) in enumerate(boxes)
                   if all(a <= b and c <= d for a, b, c, d in
                          zip(low, solid["min_mm"], solid["max_mm"], high))]
        coverage.append({**solid, "containing_box_indices": matches})
    return {"contained": bool(coverage) and all(item["containing_box_indices"] for item in coverage),
            "solid_count": len(coverage), "box_count": len(boxes), "solid_coverage": coverage}


class _PointSolidIndex:
    """Conservative broad phase only; each candidate still uses exact isInside.

    BRepBndLib.Add expands bounds by subshape tolerances. It can therefore
    produce false candidates, but cannot authorize a point as occupied.
    """

    def __init__(self, solids, tolerance):
        self.tolerance = tolerance
        records = []
        for solid in solids:
            bounds = Bnd_Box()
            BRepBndLib.Add_s(solid.wrapped, bounds, False)
            low_high = bounds.Get()
            records.append((solid, low_high[:3], low_high[3:]))
        self.root = self._node(records)

    @classmethod
    def _node(cls, records):
        if not records:
            return None
        low = tuple(min(r[1][a] for r in records) for a in range(3))
        high = tuple(max(r[2][a] for r in records) for a in range(3))
        if len(records) <= 8:
            return low, high, records, None, None
        axis = max(range(3), key=lambda a: high[a]-low[a])
        records.sort(key=lambda r: r[1][axis]+r[2][axis])
        middle = len(records)//2
        return low, high, None, cls._node(records[:middle]), cls._node(records[middle:])

    def contains(self, coordinates):
        vector = cq.Vector(*coordinates)

        def visit(node):
            if node is None:
                return False
            low, high, records, left, right = node
            if any(p < a-self.tolerance or p > b+self.tolerance
                   for p, a, b in zip(coordinates, low, high)):
                return False
            if records is None:
                return visit(left) or visit(right)
            for solid, low, high in records:
                if all(a-self.tolerance <= p <= b+self.tolerance
                       for p, a, b in zip(coordinates, low, high)) and solid.isInside(vector, self.tolerance):
                    return True
            return False

        return visit(self.root)


def load_step(path: Path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError("STEP reader could not read the artifact")
    # Set the reader's target explicitly; other modules may change OCC defaults.
    reader.SetSystemLengthUnit(1.0)
    if reader.TransferRoots() < 1:
        raise ValueError("STEP contains no transferable roots")
    return cq.Shape.cast(reader.OneShape())


def _leaves(shape):
    """Reject dangling surfaces/edges hidden beside otherwise valid solids."""
    if shape.ShapeType() in ("Compound", "CompSolid"):
        it = TopoDS_Iterator(shape.wrapped)
        while it.More():
            yield from _leaves(cq.Shape.cast(it.Value()))
            it.Next()
    else:
        yield shape


def validity(shape, volume_tolerance: float) -> dict:
    leaves = list(_leaves(shape))
    solids = shape.Solids()
    volumes = [s.Volume() for s in solids]
    return {
        "valid": bool(leaves) and all(s.ShapeType() == "Solid" for s in leaves)
        and shape.isValid() and bool(solids)
        and all(s.isValid() for s in solids)
        and all(math.isfinite(v) and v > volume_tolerance for v in volumes),
        "solid_count": len(solids),
        "non_solid_leaf_types": [s.ShapeType() for s in leaves if s.ShapeType() != "Solid"],
        "solid_volumes_mm3": volumes,
    }


def _bounds_separated(a, b):
    return any(hi < low or end < start for start, hi, low, end in
               zip(a["min_mm"], a["max_mm"], b["min_mm"], b["max_mm"]))


def _checked_boolean(operation_type, a, b):
    operation = operation_type()
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(a.wrapped)
    tools.Append(b.wrapped)
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.SetNonDestructive(True)
    operation.SetRunParallel(False)
    operation.Build()
    if not operation.IsDone():
        raise ValueError("OCC Boolean operation did not finish")
    result = operation.Shape()
    if result.IsNull():
        # A failed complex Common has produced Null on the real input. Null is
        # never interpreted as a zero-volume success; an empty valid Compound is.
        raise ValueError("OCC Boolean returned a Null shape; no zero-volume conclusion")
    result = cq.Shape.cast(result)
    if not result.isValid():
        raise ValueError("OCC produced an invalid Boolean result")
    return result


def intersection_volume(a, b, *, details=None) -> float:
    """Exact per-solid Commons; the sum bounds union volume from above.

    Input assemblies may contain overlapping/touching components. One large
    compound Boolean is avoided, without silently discarding any source solid.
    A sum of zero proves no volumetric overlap; a nonzero sum is explicitly not
    advertised as the exact volume of a fused geometric union.
    """
    if a.wrapped.IsNull() or b.wrapped.IsNull():
        raise ValueError("Null Boolean input")
    left, right = a.Solids(), b.Solids()
    left_bounds, right_bounds = solid_bounds(a), solid_bounds(b)
    values, checks, rejected = [], [], 0
    for i, solid_a in enumerate(left):
        for j, solid_b in enumerate(right):
            if _bounds_separated(left_bounds[i], right_bounds[j]):
                rejected += 1
                continue
            try:
                common = _checked_boolean(BRepAlgoAPI_Common, solid_a, solid_b)
            except Exception as exc:
                raise ValueError(f"Exact Common failed for source solid pair ({i}, {j}): {exc}") from exc
            volume = math.fsum(abs(s.Volume()) for s in common.Solids())
            if not math.isfinite(volume):
                raise ValueError("Non-finite per-solid Common volume")
            values.append(volume)
            checks.append({"solid_index_a": i, "solid_index_b": j, "common_volume_mm3": volume})
    total = math.fsum(values)
    if details is not None:
        details.update(method="OCC_conservative_bounds_rejection_and_exact_per_solid_Common_sum",
                       non_destructive_booleans=True,
                       input_solid_counts=[len(left), len(right)],
                       aabb_disjoint_pair_count=rejected, exact_common_pair_count=len(checks),
                       exact_common_pairs=checks,
                       volume_semantics="sum_of_per_solid_intersections_upper_bound_on_union_volume",
                       common_volume_sum_mm3=total)
    return total


def difference_volume(a, b) -> float:
    return math.fsum(abs(s.Volume()) for s in _difference_solids(a, b))


def _difference_solids(a, b):
    # Preserve the Python wrapper of unchanged solids so weak bounds caches
    # survive multiple disjoint contact-region subtractions.
    left = [a] if a.ShapeType() == "Solid" else a.Solids()
    right = [b] if b.ShapeType() == "Solid" else b.Solids()
    left_bounds, right_bounds = solid_bounds(a), solid_bounds(b)
    results = []
    for i, solid in enumerate(left):
        fragments = [solid]
        for j, cutter in enumerate(right):
            if _bounds_separated(left_bounds[i], right_bounds[j]):
                continue
            fragments = [piece for current in fragments
                         for piece in _checked_boolean(BRepAlgoAPI_Cut, current, cutter).Solids()]
        results.extend(fragments)
    return results


def distances_outside_regions(a, b, regions) -> list[dict]:
    """Bound a touch exemption spatially, including contacts on other faces.

    Removing a region from each solid and comparing each remainder with the
    other original solid cannot hide a contact outside that region. The region
    must include a small buffer; borderline cut faces are conservative failures.
    """
    measured = []
    for index, (source, other) in enumerate(((a, b), (b, a))):
        fragments = source.Solids()
        for region in regions:
            fragments = [piece for current in fragments for piece in _difference_solids(current, region)]
        if fragments:
            remainder = cq.Compound.makeCompound(fragments)
            measured.append({"remainder_of_part_index": index, **distance(remainder, other)})
    return measured


def distance(a, b) -> dict:
    """One exact solver execution per immutable ordered B-rep shape pair.

    Verification-owned shapes remain immutable: Boolean operations use
    non-destructive mode and transformations create new shapes. Cached values
    are numeric tuples; caller-added report fields cannot modify the cache.
    Location/orientation snapshots additionally reject changed placements.
    """
    if a.wrapped.IsNull() or b.wrapped.IsNull():
        raise ValueError("Null minimum-distance input")
    by_second = _DISTANCE_CACHE.setdefault(a, weakref.WeakKeyDictionary())
    previous = by_second.get(b)
    if previous and previous[0].IsEqual(a.wrapped) and previous[1].IsEqual(b.wrapped):
        value, point_a, point_b = previous[2]
        return {"minimum_distance_mm": value, "closest_points_mm": [list(point_a), list(point_b)]}
    operation = BRepExtrema_DistShapeShape()
    operation.LoadS1(a.wrapped)
    operation.LoadS2(b.wrapped)
    completed = operation.Perform()
    if not completed or not operation.IsDone() or operation.NbSolution() < 1:
        raise ValueError("OCC minimum-distance operation failed")
    p, q = operation.PointOnShape1(1), operation.PointOnShape2(1)
    value = operation.Value()
    coordinates = ((p.X(), p.Y(), p.Z()), (q.X(), q.Y(), q.Z()))
    if not all(math.isfinite(number) for number in (value, *coordinates[0], *coordinates[1])):
        raise ValueError("OCC minimum distance is not finite")
    by_second[b] = (a.wrapped.Located(a.wrapped.Location()), b.wrapped.Located(b.wrapped.Location()),
                    (value, *coordinates))
    return {"minimum_distance_mm": value,
            "closest_points_mm": [list(point) for point in coordinates]}


def box(minimum, maximum):
    return cq.Solid.makeBox(*(b - a for a, b in zip(minimum, maximum)), cq.Vector(*minimum))


def cylinder(center, axis, diameter, span):
    start = [p + span[0] * v for p, v in zip(center, axis)]
    return cq.Solid.makeCylinder(diameter / 2, span[1] - span[0],
                                 cq.Vector(*start), cq.Vector(*axis))


def transform(shape, matrix):
    # Input validation has already excluded scale, skew and reflection.
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetValues(*[value for row in matrix[:3] for value in row])
    return shape.moved(cq.Location(trsf))


def _cavity_faces(shape, tolerance):
    """Cache measured cylindrical surfaces, never a pass/fail conclusion."""
    by_tolerance = _CAVITY_CACHE.setdefault(shape, {})
    if tolerance in by_tolerance:
        return by_tolerance[tolerance]
    occupied = _PointSolidIndex(shape.Solids(), tolerance)
    measured = []
    for face_index, face in enumerate(shape.Faces()):
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face.wrapped)
        full_circular_face = abs((u1 - u0) - 2 * math.pi) <= 1e-6
        cyl = surface.Cylinder()
        direction = cyl.Axis().Direction()
        d = [direction.X(), direction.Y(), direction.Z()]
        point = cyl.Location()
        location = [point.X(), point.Y(), point.Z()]
        mid_v = (v0 + v1) / 2
        radius = cyl.Radius()
        # Exterior cylinders have occupied centers or empty material outside.
        mid = [p + mid_v * a for p, a in zip(location, d)]
        if occupied.contains(mid):
            continue
        outside_material = True
        for fraction in (0.125, 0.375, 0.625, 0.875):
            p = surface.Value(u0 + fraction * (u1 - u0), mid_v)
            radial = [p.X() - mid[0], p.Y() - mid[1], p.Z() - mid[2]]
            point_outside_radius = [
                m + r * (1 + max(10 * tolerance, radius * 1e-4) / radius)
                for m, r in zip(mid, radial)
            ]
            if not occupied.contains(point_outside_radius):
                outside_material = False
                break
        if not outside_material:
            continue
        starts = [surface.Value(u, mid_v) for u in (u0, u1)]
        measured.append({"face_index": face_index, "diameter_mm": 2*radius,
                         "axis_point_mm": location, "axis_direction": d,
                         "v_bounds": [v0, v1], "u_span_rad": u1-u0,
                         "radial_endpoints": [[p.X()-mid[0], p.Y()-mid[1], p.Z()-mid[2]] for p in starts],
                         "full_circular_face": full_circular_face})
    by_tolerance[tolerance] = measured
    return measured


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def _complete_cylinder_groups(candidates, tolerance):
    """Merge only coaxial equal-radius/equal-span analytic angular coverage.

    This handles STEP exporters that split a full bore into two half faces.
    Overlapping copies of one half, missing angular sectors, different axial
    spans or distinct axes cannot produce full 2*pi coverage.
    """
    groups = []
    for candidate in candidates:
        if candidate["full_circular_face"]:
            groups.append([candidate])
            continue
        for group in groups:
            first = group[0]
            if first["full_circular_face"]:
                continue
            aligned = abs(_dot(first["axis_direction"], candidate["axis_direction"])) >= 1-1e-14
            delta = [a-b for a, b in zip(candidate["axis_point_mm"], first["axis_point_mm"])]
            offset = math.sqrt(sum(v*v for v in _cross(delta, first["axis_direction"])))
            if (aligned and offset <= tolerance
                and abs(first["diameter_mm"]-candidate["diameter_mm"]) <= tolerance
                and all(abs(a-b) <= tolerance for a, b in zip(first["span_mm"], candidate["span_mm"]))):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    answer = []
    for group in groups:
        first = group[0]
        if len(group) == 1:
            answer.append(first)
            continue
        direction = first["axis_direction"]
        reference = [1, 0, 0] if abs(direction[0]) < 0.9 else [0, 1, 0]
        e1 = _cross(direction, reference)
        norm = math.sqrt(_dot(e1, e1))
        e1 = [v/norm for v in e1]
        e2 = _cross(direction, e1)
        intervals = []
        for item in group:
            endpoint = 0 if _dot(direction, item["axis_direction"]) >= 0 else 1
            radial = item["radial_endpoints"][endpoint]
            start = math.atan2(_dot(radial, e2), _dot(radial, e1)) % (2*math.pi)
            end = start+item["u_span_rad"]
            if end <= 2*math.pi:
                intervals.append((start, end))
            else:
                intervals.extend(((start, 2*math.pi), (0, end-2*math.pi)))
        intervals.sort()
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]+1e-6:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        complete = len(merged) == 1 and merged[0][0] <= 1e-6 and merged[0][1] >= 2*math.pi-1e-6
        if complete:
            combined = dict(first)
            # Intersect the measured spans, never expand an axial face interval.
            combined.update(full_circular_face=True, merged_face_count=len(group),
                            face_indices=[item["face_index"] for item in group],
                            angular_coverage_rad=merged,
                            span_mm=[max(item["span_mm"][0] for item in group),
                                     min(item["span_mm"][1] for item in group)])
            answer.append(combined)
        else:
            answer.extend(group)
    # Internal endpoints are not part of the report contract.
    return [{k: v for k, v in item.items() if k != "radial_endpoints"} for item in answer]


def bore_candidates(shape, center, axis, tolerance) -> list[dict]:
    """Measure analytic cavities; a separate solid probe still proves clearance."""
    candidates = []
    for raw in _cavity_faces(shape, tolerance):
        d, location = raw["axis_direction"], raw["axis_point_mm"]
        alignment = _dot(d, axis)
        angle = math.degrees(math.acos(min(1.0, abs(alignment))))
        delta = [p-c for p, c in zip(location, center)]
        along = _dot(delta, axis)
        offset = math.sqrt(sum((p-along*a)**2 for p, a in zip(delta, axis)))
        spans = sorted(along+v*alignment for v in raw["v_bounds"])
        candidates.append({k: v for k, v in raw.items() if k != "v_bounds"})
        candidates[-1].update(axis_offset_mm=offset, axis_angle_deg=angle, span_mm=spans)
    candidates = _complete_cylinder_groups(candidates, tolerance)
    return sorted(candidates, key=lambda c: (c["axis_angle_deg"], c["axis_offset_mm"]))


def linear_brep_sweep(shape, translation):
    """Continuous translation union using the real BRep and its boundary faces.

    For any compact solid S, S+[0,v] equals S union the translations of
    boundary(S) along [0,v]. Planar faces with nonzero normal dot v create
    actual prism solids (inner wires preserve holes). Faces exactly parallel
    to v, and cylindrical faces whose analytic axes are exactly parallel to v,
    sweep zero-volume boundary surfaces and need no volume component: their
    closure is already bounded by the adjacent nonparallel face prisms.

    Other curved faces fail closed. The returned compound represents a union
    for occupied-space/distance tests; overlapping prism volumes must never be
    interpreted as a fused union volume. No discrete pose sampling is used.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.gp import gp_Vec

    if (not isinstance(translation, list) or len(translation) != 3 or
            any(type(v) not in (float, int) or not math.isfinite(v) for v in translation) or
            sum(v != 0 for v in translation) != 1):
        raise ValueError("BRep sweep supports one finite nonzero axis-aligned translation")
    axis = next(i for i, value in enumerate(translation) if value != 0)
    pieces = list(shape.Solids())
    if not pieces or not shape.isValid():
        raise ValueError("Moving BRep must contain valid actual solids")
    evidence = {"source_solid_count": len(pieces), "source_face_count": 0,
                "prism_faces": [], "zero_volume_parallel_faces": [],
                "unsupported_curved_faces": [], "sampling": "none",
                "union_semantics": "source solid union all actual nonparallel boundary-face prisms; components may overlap"}
    for index, face in enumerate(shape.Faces()):
        evidence["source_face_count"] += 1
        surface = BRepAdaptor_Surface(face.wrapped)
        kind = surface.GetType()
        if kind == GeomAbs_Plane:
            normal = surface.Plane().Axis().Direction()
            component = (normal.X(), normal.Y(), normal.Z())[axis]
            if component == 0.0:
                evidence["zero_volume_parallel_faces"].append({"face_index": index, "type": "plane",
                    "analytic_normal_dot_translation_axis": 0.0})
                continue
        elif kind == GeomAbs_Cylinder:
            direction = surface.Cylinder().Axis().Direction()
            coordinates = (direction.X(), direction.Y(), direction.Z())
            if all(coordinates[i] == 0.0 for i in range(3) if i != axis):
                evidence["zero_volume_parallel_faces"].append({"face_index": index, "type": "cylinder",
                    "analytic_axis_direction": list(coordinates)})
                continue
            raise ValueError(f"Unsupported curved sweep face {index}: cylinder axis is not exactly parallel to translation")
        else:
            raise ValueError(f"Unsupported curved sweep face {index}: analytic surface type {kind}")
        operation = BRepPrimAPI_MakePrism(face.wrapped, gp_Vec(*translation), True, True)
        if not operation.IsDone() or operation.Shape().IsNull():
            raise ValueError(f"Boundary-face prism {index} could not be constructed")
        prism = cq.Shape.cast(operation.Shape())
        prism_solids = prism.Solids()
        if len(prism_solids) != 1 or not prism.isValid():
            raise ValueError(f"Boundary-face prism {index} is not one valid solid")
        value = prism_solids[0].Volume()
        if not math.isfinite(value) or value == 0.0:
            raise ValueError(f"Nonparallel face {index} produced no finite positive-volume prism")
        # Opposite boundary orientation can reverse a new extrusion's solid;
        # reversing that fresh result changes orientation, never occupied space.
        if value < 0:
            prism = cq.Shape.cast(prism.wrapped.Reversed())
        if not prism.isValid() or prism.Volume() <= 0:
            raise ValueError(f"Boundary-face prism {index} has invalid orientation")
        pieces.append(prism)
        evidence["prism_faces"].append({"face_index": index, "type": "plane",
                                        "prism_volume_mm3": abs(value)})
    if not evidence["prism_faces"]:
        raise ValueError("No nonparallel boundary faces establish a complete solid sweep")
    evidence["component_solid_count"] = len(pieces)
    return cq.Compound.makeCompound(pieces), evidence


_CONSERVATIVE_BOUNDS_CACHE = weakref.WeakKeyDictionary()


def conservative_bounds(shape):
    """OCC geometry bounds including shape tolerances, never mesh bounds.

    Cached shapes are immutable in verification. A placement/orientation
    snapshot additionally rejects a moved wrapper before reusing its bounds.
    """
    if shape.wrapped.IsNull():
        raise ValueError("Null conservative-bound input")
    previous = _CONSERVATIVE_BOUNDS_CACHE.get(shape)
    if previous and previous[0].IsEqual(shape.wrapped):
        low, high = previous[1]
    else:
        bounds = Bnd_Box()
        BRepBndLib.Add_s(shape.wrapped, bounds, False)
        if bounds.IsVoid():
            raise ValueError("Conservative BRep bounds are empty")
        values = bounds.Get()
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Conservative BRep bounds must be finite")
        low, high = tuple(values[:3]), tuple(values[3:])
        if any(a > b for a, b in zip(low, high)):
            raise ValueError("Conservative BRep bounds are inverted")
        _CONSERVATIVE_BOUNDS_CACHE[shape] = (shape.wrapped.Located(shape.wrapped.Location()), (low, high))
    return {"min_mm": list(low), "max_mm": list(high)}


def separation_lower_bound(a, b):
    """Conservative Euclidean distance lower bound from OCC geometry bounds.

    Zero or an insufficient bound makes no clearance claim. Only a strict
    positive bound above every required threshold may replace an exact solver.
    """
    left, right = conservative_bounds(a), conservative_bounds(b)
    gaps = [max(0.0, left["min_mm"][i] - right["max_mm"][i],
                right["min_mm"][i] - left["max_mm"][i]) for i in range(3)]
    value = math.sqrt(math.fsum(gap * gap for gap in gaps))
    if not math.isfinite(value):
        raise ValueError("Conservative distance lower bound is not finite")
    return {"minimum_distance_lower_bound_mm": value, "axis_separation_lower_bounds_mm": gaps,
            "conservative_bounds_mm": [left, right],
            "bound_method": "OCC_BRepBndLib_Add_without_triangulation_including_shape_tolerances",
            "distance_semantics": "lower_bound_only_not_an_exact_minimum_or_closest_points"}
