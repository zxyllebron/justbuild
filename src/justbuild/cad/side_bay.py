"""Explicit side-port U bays with retained openings and open lower edges.

This constructs shell material/voids. It never identifies the connector or
asserts a real plug/button motion envelope from a component bounding box.
"""
from .request import need


def surface_offset_mm(access):
    """Outward local-plane offset; explicit positive protrusion moves it inward."""
    return -access["min_protrusion_mm"] if "min_protrusion_mm" in access else access["max_recess_mm"]


def side_bay(cq, port, manufacturing, outer_bounds, lid_z, all_ports=None):
    """Return (pocket, positive_material, base_seat, evidence) in the board frame.

    A horizontal section is a U: local face panel plus two return walls.
    Its lower edge is open to avoid reserving a crossbar through a screen or
    carrier. The original opening cutter must subsequently pierce the panel.
    """
    sa = port["surface_access"]
    axis = "xy".index(port["face"][0]); tangent = 1-axis
    sign = -1 if port["face"].endswith("min") else 1
    center = port["center_mm"]
    wall, tol = manufacturing["wall_thickness_mm"], manufacturing["geometry_tolerance_mm"]
    outside = outer_bounds["min_mm"] if sign < 0 else outer_bounds["max_mm"]
    global_plane = outside[axis]
    local_plane = center[axis]+sign*surface_offset_mm(sa)
    inner_plane = local_plane-sign*sa["panel_thickness_mm"]
    need(sign*(global_plane-local_plane) > tol,
         "Side bay requires a genuinely recessed global wall", feature_id=port["id"])
    half = port["width_mm"]/2+sa["recess_margin_mm"]
    horizontal_lo, horizontal_hi = center[tangent]-half, center[tangent]+half
    opening_lo = center[2]-port["height_mm"]/2
    panel_bottom, top = sa["open_bottom_z_mm"], outer_bounds["max_mm"][2]
    group = [port]
    remaining = [other for other in (all_ports or []) if other.get("id") != port["id"]
                 and other.get("face") == port["face"]
                 and other.get("surface_access", {}).get("mode") == "side_u_bay"]
    def interval(other):
        half_width = other["width_mm"]/2+other["surface_access"]["recess_margin_mm"]
        return other["center_mm"][tangent]-half_width, other["center_mm"][tangent]+half_width
    changed = True
    while changed:
        changed = False
        for other in list(remaining):
            a, b = interval(other)
            if any(min(b, interval(member)[1])-max(a, interval(member)[0]) >= -tol for member in group):
                group.append(other);remaining.remove(other);changed = True
    # Overlapping side pockets share their full lower opening. Otherwise a
    # deeper neighboring pocket can sever a tiny return-wall island beneath it.
    pocket_bottom = min(min(other["surface_access"]["open_bottom_z_mm"],
                            other["center_mm"][2]-other["height_mm"]/2-other["surface_access"]["recess_margin_mm"])
                        for other in group)
    need(panel_bottom < center[2]+port["height_mm"]/2-tol,
         "Side bay lower edge leaves no material adjacent to the original opening", feature_id=port["id"])

    def box(lo, hi):
        need(all(hi[i] > lo[i] for i in range(3)), "Degenerate side bay region", feature_id=port["id"])
        return cq.Solid.makeBox(*(hi[i]-lo[i] for i in range(3)), cq.Vector(*lo))
    pocket_lo, pocket_hi = list(center), list(center)
    pocket_lo[axis], pocket_hi[axis] = sorted((global_plane+sign*tol, local_plane))
    pocket_lo[tangent], pocket_hi[tangent] = horizontal_lo, horizontal_hi
    pocket_lo[2], pocket_hi[2] = pocket_bottom, top+tol
    pocket = box(pocket_lo, pocket_hi)
    pane_lo, pane_hi = list(center), list(center)
    pane_lo[axis], pane_hi[axis] = sorted((local_plane, inner_plane))
    pane_lo[tangent], pane_hi[tangent] = horizontal_lo, horizontal_hi
    pane_lo[2], pane_hi[2] = panel_bottom, top
    material = box(pane_lo, pane_hi)
    returns = []
    for low, high in ((horizontal_lo-wall, horizontal_lo), (horizontal_hi, horizontal_hi+wall)):
        lo, hi = list(pane_lo), list(pane_hi)
        lo[axis], hi[axis] = sorted((global_plane, inner_plane))
        lo[tangent], hi[tangent] = low, high
        part = box(lo, hi)
        material = material.fuse(part)
        returns.append({"min_mm": lo, "max_mm": hi})
    # The panel hangs from the lid so a PCB whose connector sits slightly
    # inside its outline can still be installed before the panel is present.
    # The base's receiving slot has a real sliding gap on both horizontal axes.
    clearance = sa["sliding_clearance_mm"]
    seat_lo, seat_hi = list(pane_lo), list(pane_hi)
    seat_lo[axis], seat_hi[axis] = sorted((global_plane+sign*clearance, inner_plane-sign*clearance))
    seat_lo[tangent], seat_hi[tangent] = horizontal_lo-wall-clearance, horizontal_hi+wall+clearance
    seat_lo[2], seat_hi[2] = panel_bottom-clearance, lid_z+tol
    seat = box(seat_lo, seat_hi)
    return pocket, material, seat, {"feature_id": port["id"], "mode": "side_u_bay", "face": port["face"],
        "mount_part": "lid", "sliding_clearance_mm": clearance,
        "merged_bay_group_ids": sorted(other["id"] for other in group),
        "base_receiving_slot_bounds": {"min_mm": seat_lo, "max_mm": seat_hi},
        "local_panel_outer_plane_mm": local_plane, "local_panel_inner_plane_mm": inner_plane,
        "panel_thickness_mm": sa["panel_thickness_mm"], "open_bottom_z_mm": panel_bottom,
        "min_protrusion_mm": sa.get("min_protrusion_mm"),
        "signed_protrusion_mm": sign*(center[axis]-local_plane),
        "panel_bounds": {"min_mm": pane_lo, "max_mm": pane_hi}, "return_wall_bounds": returns,
        "pocket_bounds": {"min_mm": pocket_lo, "max_mm": pocket_hi},
        "original_opening_mm": {"width": port["width_mm"], "height": port["height_mm"]},
        "void_scope": "base/lid bay pocket only; carrier/screen access remains governed by original service envelope",
        "source": sa["source"], "actual_plug_and_actuation_verified": False}
