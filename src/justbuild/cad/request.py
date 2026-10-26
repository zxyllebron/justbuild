"""Strict, kernel-independent validation of the deliberately small CAD v0.1 dialect."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


class CadError(Exception):
    def __init__(self, code: str, message: str, status: str = "needs_input",
                 feature_id: str | None = None, **evidence: Any):
        super().__init__(message)
        self.code, self.status, self.feature_id = code, status, feature_id
        self.evidence = evidence


def need(condition: bool, message: str, code: str = "INVALID_CONSTRAINT",
         status: str = "needs_input", **evidence: Any) -> None:
    if not condition:
        raise CadError(code, message, status, **evidence)


def obj(value: Any, name: str) -> dict:
    need(isinstance(value, dict), f"{name} must be an object")
    return value


def text(value: Any, name: str) -> str:
    need(isinstance(value, str) and bool(value.strip()), f"{name} is required")
    return value


def number(value: Any, name: str, positive: bool = True) -> float:
    need(type(value) in (int, float) and math.isfinite(value), f"{name} must be finite")
    need(not positive or value > 0, f"{name} must be positive")
    return float(value)


def vector(value: Any, name: str) -> list[float]:
    need(isinstance(value, list) and len(value) == 3, f"{name} must have three numbers")
    return [number(v, name, False) for v in value]


def bounds(value: Any, name: str) -> tuple[list[float], list[float]]:
    value = obj(value, name)
    lo, hi = vector(value.get("min_mm"), name), vector(value.get("max_mm"), name)
    need(all(b > a for a, b in zip(lo, hi)), f"{name}: max must exceed min on every axis")
    text(value.get("source"), f"{name}.source")
    return lo, hi


FACE_DIRECTIONS = {
    "x_min": [1, 0, 0], "x_max": [-1, 0, 0],
    "y_min": [0, 1, 0], "y_max": [0, -1, 0],
    "z_min": [0, 0, 1], "z_max": [0, 0, -1],
}


def validate(request: Any, request_dir: Path) -> dict:
    r = obj(request, "request")
    need(r.get("schema_version") == "0.1", "Only schema_version 0.1 is supported",
         "UNSUPPORTED_SCHEMA", "unsupported")
    text(r.get("job_id"), "job_id")
    board = obj(r.get("board"), "board")
    need(board.get("units") == "mm", "Declare board.units=mm; STEP is read into millimeters")
    step = Path(text(board.get("step_path"), "board.step_path")).expanduser()
    step = (request_dir / step).resolve() if not step.is_absolute() else step.resolve()
    need(step.suffix.lower() in (".step", ".stp"), "board.step_path must be STEP/STP")
    need(step.is_file(), "STEP input does not exist", "MISSING_STEP", path=str(step))
    metadata = obj(board.get("metadata"), "board.metadata")
    index = metadata.get("pcb_solid_index")
    need(type(index) is int and index >= 0, "Select a zero-based board.metadata.pcb_solid_index")
    text(metadata.get("pcb_selection_source"), "board.metadata.pcb_selection_source")
    need(metadata.get("pcb_geometry_mode", "rectangular_declared_holes")
         in ("rectangular_declared_holes", "original_outline"),
         "pcb_geometry_mode must be rectangular_declared_holes or original_outline",
         "UNSUPPORTED_PCB_GEOMETRY_MODE", "unsupported")
    need(metadata.get("source_export_mode", "reencode") in ("reencode", "native_identity_bytes"),
         "Unsupported source_export_mode", "UNSUPPORTED_SOURCE_EXPORT", "unsupported")
    c = obj(r.get("constraints"), "constraints")
    need(c.get("confirmed") is True, "The proposal must be explicitly confirmed",
         "PROPOSAL_UNCONFIRMED")
    need(c.get("structure") == "single_pcb_screw_lid", "Only single_pcb_screw_lid is supported",
         "UNSUPPORTED_STRUCTURE", "unsupported")
    frame = obj(c.get("board_frame"), "board_frame")
    origin = vector(frame.get("origin_mm"), "board_frame.origin_mm")
    axes = [vector(frame.get(f"{a}_axis"), f"board_frame.{a}_axis") for a in "xyz"]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    for i, a in enumerate(axes):
        need(abs(dot(a, a) - 1) < 1e-8, "Frame axes must be unit vectors")
        for b in axes[:i]:
            need(abs(dot(a, b)) < 1e-8, "Frame axes must be orthogonal")
    x, y, z = axes
    cross = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
    need(dot(cross, z) > 1 - 1e-8, "Frame must be right handed")
    if metadata.get("source_export_mode") == "native_identity_bytes":
        need(frame == {"origin_mm":[0,0,0],"x_axis":[1,0,0],"y_axis":[0,1,0],"z_axis":[0,0,1]},
             "Native source byte preservation requires an exact identity board frame")
    review = obj(c.get("feature_review"), "constraints.feature_review")
    for name in ("mounting_holes", "ports", "keepouts"):
        need(review.get(f"{name}_complete") is True,
             f"Explicitly review {name}; an empty list alone is insufficient", "FEATURE_REVIEW_REQUIRED")
        need(isinstance(c.get(name), list), f"constraints.{name} must be a list")
    text(review.get("source"), "feature_review.source")
    need(len(c["mounting_holes"]) >= 3, "At least three confirmed PCB mounting holes are required")
    ids: set[str] = set()
    for group in ("mounting_holes", "ports", "keepouts"):
        for entry in c[group]:
            obj(entry, group)
            fid = text(entry.get("id"), f"{group}.id")
            need(fid not in ids, "Feature IDs must be unique", feature_id=fid)
            ids.add(fid)
            text(entry.get("source"), f"{fid}.source")
    for h in c["mounting_holes"]:
        vector(h.get("center_mm"), f"{h['id']}.center_mm")
        axis = vector(h.get("axis"), f"{h['id']}.axis")
        need(abs(axis[0]) < 1e-8 and abs(axis[1]) < 1e-8 and abs(abs(axis[2])-1) < 1e-8,
             "Only through PCB holes parallel to board Z are supported", "UNSUPPORTED_HOLE", "unsupported",
             feature_id=h["id"])
        need(h.get("reference") == "pcb", "Hole reference must be 'pcb'", feature_id=h["id"])
        number(h.get("diameter_mm"), f"{h['id']}.diameter_mm")
    points = [h["center_mm"][:2] for h in c["mounting_holes"]]
    # At least three non-collinear centers. No inferred relocation of a confirmed hole.
    a = points[0]
    need(any(abs((b[0]-a[0])*(d[1]-a[1])-(b[1]-a[1])*(d[0]-a[0])) > 1e-6
             for b in points[1:] for d in points[1:]), "PCB supports must be non-collinear")
    for p in c["ports"]:
        access = p.get("access")
        need(access in ("external", "internal", "maintenance", "covered"), "Unknown port access mode")
        if access in ("internal", "covered"):
            continue
        need(p.get("shape") in ("rectangle", "circle"), "Supported openings: rectangle or circle",
             "UNSUPPORTED_OPENING", "unsupported", feature_id=p["id"])
        face = p.get("face")
        need(face in FACE_DIRECTIONS, "Opening face must be x_min/x_max/y_min/y_max/z_min/z_max")
        center = vector(p.get("center_mm"), f"{p['id']}.center_mm")
        direction = vector(p.get("insertion_direction"), f"{p['id']}.insertion_direction")
        need(all(abs(a-b) < 1e-8 for a, b in zip(direction, FACE_DIRECTIONS[face])),
             "Insertion direction must point from exterior toward the stated face", feature_id=p["id"])
        dims = ("width_mm", "height_mm") if p["shape"] == "rectangle" else ("diameter_mm",)
        for dim in dims:
            number(p.get(dim), f"{p['id']}.{dim}")
        lo, hi = bounds(p.get("service_envelope"), f"{p['id']}.service_envelope")
        envelope = p["service_envelope"]
        need(envelope.get("shape", "box") == "box", "Only box service envelopes are supported",
             "UNSUPPORTED_SERVICE_ENVELOPE", "unsupported", feature_id=p["id"])
        axis_index = "xyz".index(face[0])
        need(all(lo[i] <= center[i] <= hi[i] for i in range(3) if i != axis_index),
             "Service envelope must contain the mating point on both tangential axes", feature_id=p["id"])
        # A service volume starts at the mating/service plane and extends outward.
        end = hi[axis_index] if face.endswith("min") else lo[axis_index]
        need(abs(end-center[axis_index]) < 1e-8,
             "Service envelope must end at the opening's mating plane", feature_id=p["id"])
    for k in c["keepouts"]:
        need(k.get("shape") == "box", "Only axis-aligned box keepouts are supported",
             "UNSUPPORTED_KEEPOUT", "unsupported", feature_id=k["id"])
        need(k.get("purpose") == "no_material", "Keepout purpose must be no_material")
        bounds(k, k["id"])
    m = obj(c.get("manufacturing"), "manufacturing")
    text(m.get("material_id"), "manufacturing.material_id")
    text(m.get("source"), "manufacturing.source")
    need(m.get("enclosure_fit", "legacy") in ("legacy", "compact"),
         "manufacturing.enclosure_fit must be legacy or compact", "UNSUPPORTED_ENCLOSURE_FIT", "unsupported")
    for p in c["ports"]:
        access = p.get("surface_access")
        if access is None:
            continue
        obj(access, f"{p['id']}.surface_access")
        need(p.get("access") in ("external", "maintenance"),
             "Surface access is only valid for an active opening", feature_id=p["id"])
        depth = number(access.get("max_recess_mm"), f"{p['id']}.surface_access.max_recess_mm", False)
        need(depth >= 0, "max_recess_mm must be nonnegative", feature_id=p["id"])
        if "min_protrusion_mm" in access:
            protrusion = number(access["min_protrusion_mm"], f"{p['id']}.surface_access.min_protrusion_mm", False)
            need(protrusion >= 0, "min_protrusion_mm must be nonnegative", feature_id=p["id"])
        if "recess_margin_mm" in access:
            number(access["recess_margin_mm"], f"{p['id']}.surface_access.recess_margin_mm")
        mode = access.get("mode", "recessed_panel" if "panel_thickness_mm" in access else "target_only")
        need(mode in ("target_only", "recessed_panel", "side_u_bay"), "Unsupported local surface access mode",
             "UNSUPPORTED_SURFACE_ACCESS", "unsupported", feature_id=p["id"])
        if "panel_thickness_mm" in access:
            number(access["panel_thickness_mm"], f"{p['id']}.surface_access.panel_thickness_mm")
            need("recess_margin_mm" in access, "Local recessed panel requires recess_margin_mm", feature_id=p["id"])
            need(m.get("enclosure_fit") == "compact", "Local recessed panels require compact fit", feature_id=p["id"])
        if mode == "side_u_bay":
            need(p.get("face") in ("x_min", "x_max", "y_min", "y_max") and p.get("shape") == "rectangle",
                 "Side U bays support cardinal side-face rectangular openings", "UNSUPPORTED_SIDE_BAY", "unsupported", feature_id=p["id"])
            need("panel_thickness_mm" in access and "recess_margin_mm" in access,
                 "Side U bay requires explicit panel thickness and margin", feature_id=p["id"])
            number(access.get("open_bottom_z_mm"), f"{p['id']}.surface_access.open_bottom_z_mm", False)
            number(access.get("sliding_clearance_mm"), f"{p['id']}.surface_access.sliding_clearance_mm")
            need(access.get("mount_part") == "lid", "Side U bays must hang from the removable lid", feature_id=p["id"])
            text(access.get("source"), f"{p['id']}.surface_access.source")
    for name in ("wall_thickness_mm", "base_thickness_mm", "lid_thickness_mm", "clearance_xy_mm",
                 "clearance_top_mm", "clearance_bottom_mm", "standoff_height_mm", "geometry_tolerance_mm",
                 "boolean_volume_tolerance_mm3", "mesh_linear_tolerance_mm", "mesh_angular_tolerance_rad"):
        number(m.get(name), f"manufacturing.{name}")
    tolerance = m["geometry_tolerance_mm"]
    need(tolerance <= 0.01, "geometry_tolerance_mm must be <= 0.01")
    need(m["boolean_volume_tolerance_mm3"] <= tolerance**3 * 1000,
         "Boolean volume tolerance may not exceed 1000 * geometry_tolerance_mm^3")
    need(m["mesh_angular_tolerance_rad"] <= 1, "Mesh angular tolerance must be <= 1 radian")
    for name in ("review_mesh_linear_tolerance_mm", "review_mesh_angular_tolerance_rad"):
        if name in m:
            value = number(m[name], f"manufacturing.{name}")
            need(value <= 1, f"manufacturing.{name} must be <= 1")
    need(all(m[n] > 10*tolerance for n in ("wall_thickness_mm", "base_thickness_mm",
             "lid_thickness_mm", "clearance_xy_mm", "clearance_top_mm", "clearance_bottom_mm", "standoff_height_mm")),
         "Structural dimensions and clearances must exceed ten geometry tolerances")
    for name in ("pcb_mount", "lid_fastener"):
        f = obj(m.get(name), f"manufacturing.{name}")
        need(f.get("type") == "self_tapping_blind_pilot", "Only self-tapping blind pilots are supported",
             "UNSUPPORTED_FASTENER", "unsupported")
        text(f.get("screw_id"), f"{name}.screw_id")
        for dim in ("post_diameter_mm", "pilot_diameter_mm", "pilot_depth_mm", "clearance_diameter_mm"):
            number(f.get(dim), f"{name}.{dim}")
        need(f["pilot_diameter_mm"] < f["clearance_diameter_mm"] < f["post_diameter_mm"],
             f"{name}: pilot < screw clearance < post diameter is required")
        radial_minimum = (f.get("minimum_radial_ligament_mm", m["wall_thickness_mm"])
                          if name == "pcb_mount" else m["wall_thickness_mm"])
        number(radial_minimum, f"{name}.minimum_radial_ligament_mm")
        need(radial_minimum > 10*tolerance,
             f"{name}: minimum radial ligament must exceed ten geometry tolerances")
        need((f["post_diameter_mm"]-f["clearance_diameter_mm"])/2 + 1e-12 >= radial_minimum,
             f"{name}: post radial ligament is below the declared minimum")
    need(m["pcb_mount"]["pilot_depth_mm"] < m["standoff_height_mm"]-tolerance,
         "PCB pilot must remain blind inside standoff")
    for h in c["mounting_holes"]:
        f = m["pcb_mount"]
        need(f["clearance_diameter_mm"] <= h["diameter_mm"] < f["post_diameter_mm"],
             "PCB hole must clear the screw and fit within its support", feature_id=h["id"])
    display = c.get("display")
    if display is not None:
        obj(display, "constraints.display")
        need(m.get("enclosure_fit") == "compact", "Display packaging requires compact fit")
        text(display.get("id"), "display.id")
        text(display.get("source"), "display.source")
        need(display.get("orientation") == "pcb_back_parallel", "Only pcb_back_parallel display is supported",
             "UNSUPPORTED_DISPLAY_ORIENTATION", "unsupported")
        size = vector(display.get("size_mm"), "display.size_mm")
        need(all(v > 0 for v in size), "Display size must be positive on every axis")
        for name in ("clearance_xy_mm", "clearance_z_mm", "board_to_display_gap_mm",
                     "pcb_bridge_thickness_mm", "pcb_post_height_mm"):
            need(number(display.get(name), f"display.{name}") > 10*tolerance,
                 "Display structural dimensions must exceed ten geometry tolerances")
        need(abs(display["pcb_post_height_mm"]-m["standoff_height_mm"]) <= tolerance,
             "Display PCB post height must match explicit manufacturing.standoff_height_mm")
        need(display["board_to_display_gap_mm"] >= display["pcb_post_height_mm"]+display["pcb_bridge_thickness_mm"]+display["clearance_z_mm"]-tolerance,
             "PCB bridge cannot intrude into the screen envelope/required Z clearance")
        support = display.get("support_structure", "integrated_bridges")
        need(support in ("integrated_bridges", "removable_pcb_carrier"),
             "Unsupported display support structure", "UNSUPPORTED_DISPLAY_SUPPORT", "unsupported")
        if support == "removable_pcb_carrier":
            for name in ("carrier_clearance_xy_mm", "carrier_slot_clearance_mm", "carrier_frame_width_mm"):
                need(number(display.get(name), f"display.{name}") > 10*tolerance,
                     "Carrier structural dimensions must exceed ten geometry tolerances")
            need(display.get("carrier_clamp") in ("shared_lid_m3", "shared_lid_fastener"),
                 "Only a shared lid-fastener carrier clamp is supported")
            need(display["carrier_frame_width_mm"] >= m["wall_thickness_mm"],
                 "Carrier frame width must preserve the declared wall thickness")
        face = obj(display.get("face_opening"), "display.face_opening")
        need(face.get("mode") == "prototype_edge_ledge", "Only prototype edge ledge screen opening is supported",
             "UNSUPPORTED_DISPLAY_OPENING", "unsupported")
        ledge = number(face.get("ledge_width_mm"), "display.face_opening.ledge_width_mm")
        need(2*ledge < min(size[:2]), "Display ledge must leave a positive screen opening")
        channel = obj(display.get("ribbon_channel"), "display.ribbon_channel")
        need(channel.get("face") in ("x_min", "x_max", "y_min", "y_max"),
             "Ribbon channel must exit a cardinal side face")
        number(channel.get("center_tangent_mm"), "display.ribbon_channel.center_tangent_mm", False)
        number(channel.get("width_mm"), "display.ribbon_channel.width_mm")
        number(channel.get("height_mm"), "display.ribbon_channel.height_mm")
        text(channel.get("source"), "display.ribbon_channel.source")
    for region in c.get("open_operation_regions", []):
        obj(region, "open_operation_regions entry")
        text(region.get("id"), "open_operation_regions.id")
        need(region.get("shape") == "box", "Only box open operation regions are supported")
        bounds(region, region["id"])
    lid = m["lid_fastener"]
    need(lid.get("preferred_ear_axis") in (None, "x", "y"), "preferred_ear_axis must be x or y")
    for dim in ("head_diameter_mm", "head_depth_mm"):
        number(lid.get(dim), f"lid_fastener.{dim}")
    need(lid["clearance_diameter_mm"] < lid["head_diameter_mm"] <= lid["post_diameter_mm"],
         "Lid head diameter must fit the post and exceed clearance diameter")
    need(lid["head_depth_mm"] < m["lid_thickness_mm"]-tolerance,
         "Counterbore must leave material below the screw head")
    return {"request": r, "step_path": step, "origin": origin, "axes": axes}
