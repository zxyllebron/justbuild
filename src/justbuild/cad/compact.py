"""Deterministic dimension budgeting shared with request/check-plan orchestration.

These bounds are construction inputs, not collision or physical-fit evidence.
"""
from .request import need
from .side_bay import surface_offset_mm


def fitted_component_bounds(input_bounds: list[dict], pcb_index: int, ports: list[dict],
                            enclosure_fit: str = "legacy") -> dict:
    """Retain all source bounds except confirmed active components' outward extents.

    Each source bound uses min_mm/max_mm in the confirmed board frame. A port's
    component_solid_indices and face can clamp only that outward coordinate to
    the PCB boundary. All other coordinates, and every unreferenced source
    component, remain in the size budget. Exact BRep tests must still follow.
    """
    need(bool(input_bounds) and 0 <= pcb_index < len(input_bounds), "Invalid compact input inventory")
    pcb = input_bounds[pcb_index]
    pl, ph = pcb["min_mm"], pcb["max_mm"]
    faces = {i: set() for i in range(len(input_bounds))}
    if enclosure_fit == "compact":
        for port in ports:
            if port.get("access") not in ("external", "maintenance"):
                continue
            for index in port.get("component_solid_indices", []):
                need(type(index) is int and 0 <= index < len(input_bounds),
                     "Port component_solid_indices must address retained source solids", feature_id=port["id"])
                need(index != pcb_index, "An exposed component cannot exclude the PCB boundary", feature_id=port["id"])
                faces[index].add(port["face"])
    lows, highs = [], []
    for index, bounds in enumerate(input_bounds):
        lo, hi = list(bounds["min_mm"]), list(bounds["max_mm"])
        for axis, letter in enumerate("xyz"):
            if f"{letter}_min" in faces[index]:
                lo[axis] = max(lo[axis], pl[axis])
            if f"{letter}_max" in faces[index]:
                hi[axis] = min(hi[axis], ph[axis])
        lows.append(lo)
        highs.append(hi)
    return {"min_mm": [min(pl[i], *(row[i] for row in lows)) for i in range(3)],
            "max_mm": [max(ph[i], *(row[i] for row in highs)) for i in range(3)]}


def body_and_footprint_budget(input_bounds: list[dict], pcb_index: int,
                              ports: list[dict], manufacturing: dict, *, display: dict | None = None) -> dict:
    """Compute exact main body planes and a conservative local-ear envelope.

    Ear layout is chosen by exact source/cutter Booleans later. The upper bound
    extends both XY axes to include either side-pair choice. Recess panels can
    extend outside body tangents and must separately be unioned into a moving
    lid's conservative envelope if requested.
    """
    m = manufacturing
    compact = m.get("enclosure_fit", "legacy") == "compact"
    fit = fitted_component_bounds(input_bounds, pcb_index, ports, m.get("enclosure_fit", "legacy"))
    gap, wall = m["clearance_xy_mm"], m["wall_thickness_mm"]
    radius = m["lid_fastener"]["post_diameter_mm"]/2
    reserve = 0 if compact else 2*radius
    floor = input_bounds[pcb_index]["min_mm"][2]-m["standoff_height_mm"]
    lid_z = fit["max_mm"][2]+m["clearance_top_mm"]
    inner_lo = [fit["min_mm"][i]-gap-reserve for i in (0, 1)]
    inner_hi = [fit["max_mm"][i]+gap+reserve for i in (0, 1)]
    body_lo = [inner_lo[0]-wall, inner_lo[1]-wall, floor-m["base_thickness_mm"]]
    body_hi = [inner_hi[0]+wall, inner_hi[1]+wall, lid_z+m["lid_thickness_mm"]]
    display_layout = None
    if display:
        from .display import display_layout as make_display_layout
        display_layout = make_display_layout(input_bounds[pcb_index], display)
        region = display_layout["seating_region_bounds"]
        for axis in (0, 1):
            inner_lo[axis] = min(inner_lo[axis], region["min_mm"][axis])
            inner_hi[axis] = max(inner_hi[axis], region["max_mm"][axis])
        floor = min(floor, display_layout["envelope_bounds"]["min_mm"][2]-display["clearance_z_mm"])
        body_lo = [inner_lo[0]-wall, inner_lo[1]-wall, floor-m["base_thickness_mm"]]
        body_hi = [inner_hi[0]+wall, inner_hi[1]+wall, lid_z+m["lid_thickness_mm"]]
    upper_lo, upper_hi = list(body_lo), list(body_hi)
    if compact:
        for axis in (0, 1):
            upper_lo[axis] = min(upper_lo[axis], inner_lo[axis]-2*radius)
            upper_hi[axis] = max(upper_hi[axis], inner_hi[axis]+2*radius)
        for p in ports:
            sa = p.get("surface_access", {})
            if p.get("face") == "z_max" and "panel_thickness_mm" in sa:
                dims = [p["width_mm"], p["height_mm"]]
                for axis in (0, 1):
                    half = dims[axis]/2+sa["recess_margin_mm"]+wall
                    upper_lo[axis] = min(upper_lo[axis], p["center_mm"][axis]-half)
                    upper_hi[axis] = max(upper_hi[axis], p["center_mm"][axis]+half)
                upper_lo[2] = min(upper_lo[2], p["center_mm"][2]+surface_offset_mm(sa)-sa["panel_thickness_mm"])
    return {"fitted_component_bounds": fit,
            "body_outer_bounds": {"min_mm": body_lo, "max_mm": body_hi},
            "cavity_bounds": {"min_mm": [*inner_lo, floor], "max_mm": [*inner_hi, lid_z]},
            "footprint_upper_bound": {"min_mm": upper_lo, "max_mm": upper_hi},
            "lid_mating_plane_z_mm": lid_z,
            "lid_min_z_mm": (input_bounds[pcb_index]["min_mm"][2]-display["pcb_post_height_mm"]
                              if display and display.get("support_structure") == "removable_pcb_carrier" else lid_z),
            "pcb_carrier_z_bounds_mm": ([input_bounds[pcb_index]["min_mm"][2]-display["pcb_post_height_mm"]-display["pcb_bridge_thickness_mm"],
                                          input_bounds[pcb_index]["min_mm"][2]]
                                         if display and display.get("support_structure") == "removable_pcb_carrier" else None),
            "base_lid_fastener_top_z_mm": (input_bounds[pcb_index]["min_mm"][2]-display["pcb_post_height_mm"]-display["pcb_bridge_thickness_mm"]
                                           if display and display.get("support_structure") == "removable_pcb_carrier" else lid_z),
            "display": display_layout}
