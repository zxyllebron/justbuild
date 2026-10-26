"""STEP inventory and conservative, explicitly unconfirmed feature proposals."""
from __future__ import annotations

import math
from pathlib import Path

from .contracts import digest


def default_manufacturing() -> dict:
    from .design_rules import default_fit_policy
    return {
        "enclosure_fit": "compact", "fit_policy": default_fit_policy(),
        "material_id": "prototype-unvalidated", "source": "justbuild v0.1 prototype defaults; review and calibrate before production",
        "wall_thickness_mm": 2.0, "base_thickness_mm": 2.0, "lid_thickness_mm": 3.0,
        "clearance_xy_mm": 0.5, "clearance_top_mm": 1.0, "clearance_bottom_mm": 1.0, "standoff_height_mm": 5.0,
        "geometry_tolerance_mm": 0.001, "boolean_volume_tolerance_mm3": 1e-7,
        "mesh_linear_tolerance_mm": 0.1, "mesh_angular_tolerance_rad": 0.1,
        "pcb_mount": {"type": "self_tapping_blind_pilot", "screw_id": "prototype-M2.5-self-tapping",
                      "post_diameter_mm": 7.5, "pilot_diameter_mm": 2.0, "pilot_depth_mm": 3.5, "clearance_diameter_mm": 2.7},
        "lid_fastener": {"type": "self_tapping_blind_pilot", "screw_id": "prototype-M3-self-tapping",
                         "post_diameter_mm": 8.0, "pilot_diameter_mm": 2.5, "pilot_depth_mm": 5.0,
                         "clearance_diameter_mm": 3.2, "head_diameter_mm": 6.0, "head_depth_mm": 1.5},
    }


def inspect_step(path: Path, job_id: str) -> dict:
    import cadquery as cq
    from OCP.TopAbs import TopAbs_REVERSED

    shape = cq.importers.importStep(str(path)).val()
    solids = shape.Solids()
    if not solids:
        raise ValueError("STEP contains no solids; a solid assembly is required")
    inventory = []
    for i, solid in enumerate(solids):
        b = solid.BoundingBox()
        inventory.append({"id": f"solid-{i}", "index": i, "valid": solid.isValid(),
                          "min_mm": [b.xmin, b.ymin, b.zmin], "max_mm": [b.xmax, b.ymax, b.zmax],
                          "size_mm": [b.xlen, b.ylen, b.zlen], "volume_mm3": solid.Volume()})
    candidates = [s for s in inventory if s["size_mm"][2] < min(s["size_mm"][:2]) * .2]
    pcb = max(candidates or inventory, key=lambda s: s["size_mm"][0] * s["size_mm"][1])
    bmin, bmax = pcb["min_mm"], pcb["max_mm"]
    holes, seen = [], set()
    for face in solids[pcb["index"]].Faces():
        if face.geomType() != "CYLINDER" or face.wrapped.Orientation() != TopAbs_REVERSED:
            continue
        cylinder = face._geomAdaptor().Cylinder()
        axis, loc = cylinder.Axis().Direction(), cylinder.Location()
        bb = face.BoundingBox()
        radius = cylinder.Radius()
        if abs(axis.Z()) < .999999 or bb.zlen < pcb["size_mm"][2] - .01 or not .4 <= radius <= 5:
            continue
        key = (round(loc.X(), 4), round(loc.Y(), 4), round(radius, 4))
        if key in seen:
            continue
        seen.add(key)
        holes.append({"id": f"mount-{len(holes)+1}", "reference": "pcb",
                      "center_mm": [loc.X(), loc.Y(), (bmin[2]+bmax[2])/2],
                      "axis": [0, 0, 1], "diameter_mm": radius * 2,
                      "source": "STEP cylindrical through-hole candidate; user must confirm mounting purpose"})
    # Edge proximity proposes access only. It does not identify an electrical connector type.
    ports = []
    for component in inventory:
        if component["index"] == pcb["index"]:
            continue
        lo, hi = component["min_mm"], component["max_mm"]
        distances = {"x_min": abs(lo[0]-bmin[0]), "x_max": abs(hi[0]-bmax[0]),
                     "y_min": abs(lo[1]-bmin[1]), "y_max": abs(hi[1]-bmax[1])}
        face = min(distances, key=distances.get)
        near = distances[face] < 2
        p = {"id": f"access-solid-{component['index']}", "access": "external" if near else "internal",
             "solid_index": component["index"], "label": f"元件 {component['index']}（用途待确认）",
             "component_kind": "unknown", "semantic_review_status": "pending",
             "source": "STEP edge-proximity heuristic; connector and plug envelope unverified"}
        if near:
            axis = 0 if face[0] == "x" else 1
            direction = [0, 0, 0]
            direction[axis] = 1 if face.endswith("min") else -1
            center = [(a+b)/2 for a, b in zip(lo, hi)]
            center[axis] = lo[axis] if face.endswith("min") else hi[axis]
            service_lo = [a-1 for a in lo]
            service_hi = [a+1 for a in hi]
            if face.endswith("min"):
                service_hi[axis] = center[axis]
                service_lo[axis] = center[axis] - 25
            else:
                service_lo[axis] = center[axis]
                service_hi[axis] = center[axis] + 25
            p.update({"shape": "rectangle", "face": face, "center_mm": center,
                      "insertion_direction": direction,
                      "width_mm": component["size_mm"][1-axis]+2, "height_mm": component["size_mm"][2]+2,
                      "service_envelope": {"min_mm": service_lo, "max_mm": service_hi,
                                           "source": "unverified generic 25 mm service envelope; replace with actual plug dimensions"}})
        ports.append(p)
    from .design_rules import apply_access_targets
    result = {"inventory": inventory, "input_sha256": digest(path), "proposal_notes": [
        "PCB selection and hole purpose are geometric hypotheses requiring confirmation.",
        "Generic service envelopes are not measured plug dimensions. Review every port before confirming.",
        "Thermal, RF, cable-flex and printed-material performance are not established by this import."],
        "request": {"schema_version": "0.1", "job_id": job_id,
                    "board": {"step_path": str(path), "units": "mm", "metadata": {
                        "pcb_solid_index": pcb["index"], "pcb_selection_source": "unconfirmed largest thin XY solid heuristic"}},
                    "constraints": {"confirmed": False, "structure": "single_pcb_screw_lid",
                        "board_frame": {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
                        "feature_review": {"mounting_holes_complete": False, "ports_complete": False,
                                           "keepouts_complete": False, "source": "pending user review"},
                        "mounting_holes": holes, "ports": ports, "keepouts": [], "manufacturing": default_manufacturing()}}}
    result["request"] = apply_access_targets(result["request"])
    result["proposal_notes"].append("Compact prototype fit: 0.5 mm nominal side gap, no full-perimeter screw-boss padding, buttons exposed and external connectors near flush; real operating travel remains unverified.")
    return result

