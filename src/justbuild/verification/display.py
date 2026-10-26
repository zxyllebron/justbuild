"""Independent checks for a user-dimensioned screen behind the PCB.

The screen is an explicitly declared rectangular mechanical envelope. It is not
an active-area, FFC, glass-pressure, strength or flexible-assembly model.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _number(value, label, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric millimeter data")
    if (positive and value <= 0) or (not positive and value < 0):
        raise ValueError(f"{label} is outside its permitted range")
    return float(value)


def _bounds(shape):
    b = shape.BoundingBox()
    return [b.xmin, b.ymin, b.zmin], [b.xmax, b.ymax, b.zmax]


def display_checks(request: dict, cad: dict, cad_path: Path) -> list[dict]:
    """Return standard supplemental checks, or [] for historical no-screen jobs.

    Source PCB and exported base/lid/screen STEP bytes are read independently;
    hashes are checked before and after measurements. Metadata cannot establish
    cavity emptiness or screen pose. Unsupported definitions fail closed.
    """
    constraints = request.get("constraints", {})
    if "display" not in constraints:
        return []
    checks = []
    tolerance, volume_tolerance = 0.001, 1e-7
    feature = "screen"

    def add(identity, status, measurements, *, required=True, method=None):
        checks.append({"id": identity, "category": "display", "feature_id": feature,
                       "status": status, "required": required,
                       "method": method or "independent_original_and_exported_STEP_BRep",
                       "tolerance_mm": tolerance,
                       "volume_tolerance_mm3": volume_tolerance,
                       "measurements": measurements})

    try:
        d = constraints["display"]
        if not isinstance(d, dict):
            raise ValueError("constraints.display must be an object")
        feature = d.get("id", "screen")
        if not isinstance(feature, str) or not feature:
            raise ValueError("display.id must be a nonempty string")
        if d.get("orientation") != "pcb_back_parallel":
            raise ValueError("Only the reviewed pcb_back_parallel placement is supported")
        size = d.get("size_mm")
        if not isinstance(size, list) or len(size) != 3:
            raise ValueError("display.size_mm must contain length, width, total thickness")
        length, width, thickness = [_number(v, "display.size_mm", positive=True) for v in size]
        if length < width:
            raise ValueError("display length must be the first and longer dimension")
        cxy = _number(d.get("clearance_xy_mm"), "display.clearance_xy_mm", positive=True)
        cz = _number(d.get("clearance_z_mm"), "display.clearance_z_mm", positive=True)
        gap = _number(d.get("board_to_display_gap_mm"), "display.board_to_display_gap_mm", positive=True)
        post_height = _number(d.get("pcb_post_height_mm"), "display.pcb_post_height_mm", positive=True)
        bridge_thickness = _number(d.get("pcb_bridge_thickness_mm"), "display.pcb_bridge_thickness_mm", positive=True)
        opening = d.get("face_opening", {})
        if not isinstance(opening, dict) or opening.get("mode") != "prototype_edge_ledge":
            raise ValueError("Only an explicit prototype_edge_ledge screen treatment is supported")
        ledge = _number(opening.get("ledge_width_mm"), "display.ledge_width_mm", positive=True)
        if 2 * ledge >= min(length, width):
            raise ValueError("Screen edge ledge leaves no front opening")
        ribbon = d.get("ribbon_channel", {})
        if not isinstance(ribbon, dict) or ribbon.get("face") not in ("x_min", "x_max", "y_min", "y_max"):
            raise ValueError("Display ribbon channel needs an explicit side face")
        tangent_center = ribbon.get("center_tangent_mm")
        if type(tangent_center) not in (int, float) or not math.isfinite(tangent_center):
            raise ValueError("ribbon_channel.center_tangent_mm must be finite")
        ribbon_width = _number(ribbon.get("width_mm"), "ribbon_channel.width_mm", positive=True)
        ribbon_height = _number(ribbon.get("height_mm"), "ribbon_channel.height_mm", positive=True)
        if not isinstance(ribbon.get("source"), str) or not ribbon["source"].strip():
            raise ValueError("The prototype ribbon channel requires provenance")
        m = constraints.get("manufacturing", {})
        tolerance = _number(m.get("geometry_tolerance_mm", 0.001), "geometry_tolerance_mm", positive=True)
        volume_tolerance = _number(m.get("boolean_volume_tolerance_mm3", 1e-7), "boolean_volume_tolerance_mm3", positive=True)
        if tolerance > 0.001 or volume_tolerance > 0.001:
            raise ValueError("Numerical tolerances exceed the verification maximum")
        wall = _number(m.get("wall_thickness_mm"), "wall_thickness_mm", positive=True)
        if abs(_number(m.get("standoff_height_mm"), "standoff_height_mm", positive=True) - post_height) > tolerance:
            raise ValueError("Display and manufacturing PCB post heights differ")
        mount = m.get("pcb_mount", {})
        post_diameter = _number(mount.get("post_diameter_mm"), "pcb_mount.post_diameter_mm", positive=True)
        pilot = _number(mount.get("pilot_diameter_mm"), "pcb_mount.pilot_diameter_mm", positive=True)
        if pilot >= post_diameter:
            raise ValueError("Pilot must be smaller than the PCB post")
        if gap + tolerance < post_height + bridge_thickness + cz:
            add("display_support_stack", "fail", {"board_to_display_gap_mm": gap,
                "required_post_bridge_and_clearance_mm": post_height + bridge_thickness + cz})
        if constraints.get("confirmed") is not True or cad.get("status") != "ok" or cad.get("job_id") != request.get("job_id"):
            raise ValueError("Matching confirmed request and successful CAD result are required")
        add("display_dimension_contract", "pass", {"size_mm": size,
            "orientation": d["orientation"], "clearance_xy_mm": cxy, "clearance_z_mm": cz,
            "source": "User-confirmed total screen dimensions; rectangular envelope, no active-area or cable model"},
            method="confirmed_user_dimension_contract")
    except (KeyError, TypeError, ValueError) as exc:
        add("display_definition", "not_checked", {"reason": str(exc)}, method="input_prerequisite_gate")
        return checks

    # These requirements are not made true by an empty static rectangular box.
    add("display_active_area", "not_checked", {"reason":
        "The declared screen outline does not identify active display area, glass edge pressure limits or a permitted retaining border; the edge ledge is only a prototype proposal."},
        method="missing_screen_active_area_and_retention_specification")
    add("display_ribbon_bend", "not_checked", {"reason":
        "The static channel has a declared prototype width/height only. Actual FFC length, thickness, connector position, bend radius and folded routing are not supplied."},
        method="missing_actual_FFC_and_flexible_assembly_geometry")
    checked_files = []
    try:
        from . import geometry as g
        from .engine import _matrix
        cad_path = Path(cad_path).resolve()
        cad_bytes = cad_path.read_bytes()
        if json.loads(cad_bytes) != cad:
            raise ValueError("CAD object differs from the manifest bytes")
        checked_files.append({"part_id": "cad_manifest", "path": str(cad_path),
                              "sha256": hashlib.sha256(cad_bytes).hexdigest()})
        artifacts = cad.get("artifacts", [])
        board = request["board"]
        if board.get("units") != "mm":
            raise ValueError("Source board units must be mm")
        original = Path(board["step_path"])
        if not original.is_absolute():
            raise ValueError("Standalone display checks require the original source STEP absolute path")
        provenance = cad.get("metadata", {}).get("input_provenance", {})
        source_hash = board.get("sha256") or provenance.get("step_sha256")
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise ValueError("Original source STEP needs a bound SHA-256")
        if board.get("sha256") and provenance.get("step_sha256") and board["sha256"] != provenance["step_sha256"]:
            raise ValueError("Original STEP provenance hashes disagree")

        def load(part, path, expected_hash):
            path = path.resolve()
            if path.suffix.lower() not in (".step", ".stp"):
                raise ValueError("Authoritative geometry must be STEP/STP")
            actual = _hash(path)
            if actual != expected_hash:
                raise ValueError(f"{part} STEP hash mismatch")
            checked_files.append({"part_id": part, "path": str(path), "sha256": actual})
            shape = g.load_step(path)
            validity = g.validity(shape, volume_tolerance)
            add("display_solid:" + part, "pass" if validity["valid"] else "fail", validity,
                method="OCC_BRep_validation_and_positive_solid_volumes")
            if not validity["valid"]:
                raise ValueError(f"{part} is not valid positive-volume BRep geometry")
            return shape

        def artifact(part):
            matches = [a for a in artifacts if a.get("kind") == part + "_step"]
            if len(matches) != 1 or matches[0].get("units") != "mm":
                raise ValueError(f"Exactly one mm {part}_step artifact is required")
            a = matches[0]
            path = Path(a["path"])
            return load(part, path if path.is_absolute() else cad_path.parent / path, a["sha256"])

        source = load("original_board", original, source_hash)
        frame = constraints["board_frame"]
        origin = frame["origin_mm"]
        axes = [frame[a + "_axis"] for a in "xyz"]
        if (not isinstance(origin, list) or len(origin) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in origin)):
            raise ValueError("Board frame needs a finite three-coordinate origin")
        matrix = _matrix([[*axis, -sum(v * o for v, o in zip(axis, origin))] for axis in axes] + [[0, 0, 0, 1]])
        source = g.transform(source, matrix)
        solids = source.Solids()
        index = board.get("metadata", {}).get("pcb_solid_index")
        if type(index) is not int or not 0 <= index < len(solids):
            raise ValueError("Original source requires an explicit selected PCB solid")
        pl, ph = _bounds(solids[index])
        if ph[0] - pl[0] + tolerance < ph[1] - pl[1]:
            raise ValueError("Parallel-long-edge definition currently requires PCB long edge along confirmed X")
        base, lid = artifact("base"), artifact("lid")
        carrier = artifact("pcb_carrier") if d.get("support_structure") == "removable_pcb_carrier" else None
        structure = [("base", base), ("lid", lid)] + ([("pcb_carrier", carrier)] if carrier is not None else [])
        support = carrier if carrier is not None else base
        actual_screen = artifact("screen_envelope")
        shell = g.cq.Compound.makeCompound([shape for _, shape in structure])
        sl, sh = _bounds(shell)
        center = [(pl[a] + ph[a]) / 2 for a in (0, 1)]
        low = [center[0] - length / 2, center[1] - width / 2, pl[2] - gap - thickness]
        high = [center[0] + length / 2, center[1] + width / 2, pl[2] - gap]
        screen = g.box(low, high)
        missing, extra = g.difference_volume(screen, actual_screen), g.difference_volume(actual_screen, screen)
        add("display_envelope_pose", "pass" if max(missing, extra) <= volume_tolerance else "fail",
            {"expected_min_mm": low, "expected_max_mm": high, "source_pcb_min_mm": pl,
             "source_pcb_max_mm": ph, "missing_volume_mm3": missing, "extra_volume_mm3": extra,
             "scope": "Exact rectangular envelope at the reviewed position, not actual screen internals"},
            method="original_PCB_frame_and_two_way_exact_BRep_difference")

        def empty(identity, probe, obstacles, evidence):
            rows = []
            for name, shape in obstacles:
                details = {}
                volume = g.intersection_volume(probe, shape, details=details)
                rows.append({"part_id": name, "common_volume_mm3": volume, "intersection_evidence": details})
            add(identity, "pass" if all(v["common_volume_mm3"] <= volume_tolerance for v in rows) else "fail",
                {**evidence, "obstacles": rows}, method="exact_per_solid_OCC_Common_no_positive_volume_exemption")

        empty("display_no_penetration", screen, [("original_board", source), *structure],
              {"min_mm": low, "max_mm": high, "allowed_contact": "zero-volume screen front edge contact only"})
        source_distance = g.distance(screen, source)
        add("display_component_clearance", "pass" if source_distance["minimum_distance_mm"] + tolerance >= cz else "fail",
            {**source_distance, "minimum_required_mm": cz,
             "scope": "All original STEP source solids, including bottom components"}, method="exact_OCC_minimum_distance")
        seat_low = [low[0] - cxy, low[1] - cxy, low[2]]
        seat_high = [high[0] + cxy, high[1] + cxy, high[2] + cz]
        empty("display_seating_clearance", g.box(seat_low, seat_high), structure,
              {"min_mm": seat_low, "max_mm": seat_high,
               "clearance_xy_per_side_mm": cxy, "clearance_above_screen_mm": cz,
               "scope": "Whole installed shell, including all posts and bridges; no support exemption"})
        aperture_low = [low[0] + ledge, low[1] + ledge, sl[2] - tolerance]
        aperture_high = [high[0] - ledge, high[1] - ledge, low[2] + tolerance]
        if aperture_high[2] <= aperture_low[2]:
            raise ValueError("Screen face has no actual shell plane to traverse")
        empty("display_front_opening", g.box(aperture_low, aperture_high), structure,
              {"min_mm": aperture_low, "max_mm": aperture_high,
               "prototype_opening_size_mm": [length - 2 * ledge, width - 2 * ledge],
               "scope": "Declared prototype aperture only; visible active-area fit remains unverified"})
        # A real retaining edge is required; a completely absent floor cannot pass.
        edge_low = [low[0], low[1], low[2] - cz]
        edge_high = [high[0], high[1], low[2]]
        required_edge = g.box(edge_low, edge_high).cut(g.box(
            [low[0] + ledge, low[1] + ledge, edge_low[2] - tolerance],
            [high[0] - ledge, high[1] - ledge, edge_high[2] + tolerance]))
        missing_edge = g.difference_volume(required_edge, base)
        add("display_edge_ledge", "pass" if missing_edge <= volume_tolerance else "fail",
            {"missing_support_volume_mm3": missing_edge, "contact_plane_z_mm": low[2],
             "ledge_width_mm": ledge, "scope": "Geometric edge contact only; no allowable glass pressure evidence"},
            method="exact_BRep_containment_of_declared_edge_support")
        holes = constraints.get("mounting_holes", [])
        if not holes:
            raise ValueError("Display support checks require the original PCB mounting-hole inventory")
        for h in holes:
            x, y, _ = h["center_mm"]
            ring = g.cylinder([x, y, pl[2] - post_height], [0, 0, 1], post_diameter, [0, post_height])
            ring = ring.cut(g.cylinder([x, y, pl[2] - post_height - tolerance], [0, 0, 1], pilot,
                                      [0, post_height + 2 * tolerance]))
            missing_post = g.difference_volume(ring, support)
            add("display_pcb_post:" + h["id"], "pass" if missing_post <= volume_tolerance else "fail",
                {"required_post_height_mm": post_height, "top_z_mm": pl[2], "bottom_z_mm": pl[2] - post_height,
                 "missing_annular_support_volume_mm3": missing_post,
                 "fixed_hole_center_mm": h["center_mm"], "support_part": "pcb_carrier" if carrier is not None else "base",
                 "scope": "Required short-post bearing annulus is present. Original hole datum and bore-axis checks are performed by the core verifier."},
                method="exact_BRep_containment_of_fixed_PCB_post_annulus")
        face = ribbon["face"]
        normal = "xy".index(face[0]); tangent = 1 - normal
        rl, rh = list(sl), list(sh)
        rl[tangent], rh[tangent] = tangent_center - ribbon_width / 2, tangent_center + ribbon_width / 2
        rl[2], rh[2] = high[2] + cz, high[2] + cz + ribbon_height
        if face.endswith("max"):
            rl[normal], rh[normal] = ph[normal] - wall, sh[normal] + tolerance
        else:
            rl[normal], rh[normal] = sl[normal] - tolerance, pl[normal] + wall
        empty("display_ribbon_static_channel", g.box(rl, rh), [("original_board", source), *structure],
              {"min_mm": rl, "max_mm": rh, "declared_provenance": ribbon["source"],
               "scope": "Static rectangular prototype passage only, not a full FFC fold or cable engagement proof"})
        if d.get("support_structure") != "removable_pcb_carrier":
            axial = []
            for sign in (1, -1):
                motion = [0, 0, sign * max(30, sh[2] - sl[2] + thickness + 10)]
                swept, sweep_evidence = g.linear_brep_sweep(screen, motion)
                intersection = {}
                hit = g.intersection_volume(swept, base, details=intersection)
                axial.append({"translation_mm": motion, "base_common_volume_mm3": hit,
                    "blocked": hit > volume_tolerance, "intersection_evidence": intersection,
                    "sweep_evidence": sweep_evidence})
            add("display_screen_assembly", "not_checked", {
                "reason": "The integrated-base static layout has no verified screen installation sequence. Static nonpenetration cannot establish assembly; no rotation or FFC folding was proved.",
                "screen_axial_extraction_probes_with_board_and_lid_absent": axial,
                "supported_axial_probes_both_blocked": all(p["blocked"] for p in axial)},
                method="exact_continuous_screen_BRep_axial_probe_and_missing_assembly_plan")
        add("display_geometry_provenance", "pass", {"checked_geometry": checked_files,
            "source_to_board_transform": matrix}, method="sha256_bound_original_and_exported_STEP_readback")
    except Exception as exc:
        add("display_geometry_evidence", "not_checked", {"reason": str(exc), "exception_type": type(exc).__name__},
            method="input_or_BRep_kernel_prerequisite_gate")
    finally:
        for record in checked_files:
            try:
                stable = _hash(Path(record["path"])) == record["sha256"]
            except OSError:
                stable = False
            if not stable:
                add("display_input_changed:" + record["part_id"], "fail", record, method="sha256_readback")
    return checks
