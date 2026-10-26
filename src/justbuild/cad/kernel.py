"""Headless CadQuery geometry. STEP solids, never display meshes, drive these checks."""
from __future__ import annotations

import math
import json
import struct
from pathlib import Path

from .request import CadError, need
from .side_bay import surface_offset_mm


def bbox(shape) -> dict:
    b = shape.BoundingBox()
    return {"min_mm": [b.xmin, b.ymin, b.zmin], "max_mm": [b.xmax, b.ymax, b.zmax]}


def read_step(cq, path: Path):
    # Transfer every root and explicitly choose a millimeter target system.
    # CadQuery importStep(...).val() alone silently drops later roots.
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    need(reader.ReadFile(str(path)) == IFSelect_RetDone,
         "Cannot read STEP", "STEP_IMPORT_FAILED", "failed")
    reader.SetSystemLengthUnit(1.0)
    need(reader.TransferRoots() > 0, "STEP has no transferable roots", "STEP_IMPORT_FAILED", "failed")
    return cq.Shape.cast(reader.OneShape())


def scale_glb_to_meters(path: Path) -> None:
    """CadQuery 2.6/OCC writes mm vertex values. Scale the scene, preserving its BIN chunk."""
    data = path.read_bytes()
    magic, version, total = struct.unpack_from("<4sII", data)
    size, kind = struct.unpack_from("<I4s", data, 12)
    need(magic == b"glTF" and version == 2 and total == len(data) and kind == b"JSON",
         "Invalid GLB container", "GLB_EXPORT_FAILED", "failed")
    document = json.loads(data[20:20+size])
    for scene in document["scenes"]:
        for index in scene["nodes"]:
            node = document["nodes"][index]
            need("matrix" not in node and "scale" not in node,
                 "Unexpected native GLB root transform; do not guess units", "GLB_EXPORT_FAILED", "failed")
            node["scale"] = [0.001, 0.001, 0.001]
            if "translation" in node:
                node["translation"] = [v * 0.001 for v in node["translation"]]
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    tail = data[20+size:]
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20+len(encoded)+len(tail))
                     + struct.pack("<I4s", len(encoded), b"JSON") + encoded + tail)


def export_glb_absolute(cq, assembly, path: Path, linear_mm: float, angular_rad: float) -> None:
    """Explicit display tolerances use absolute mm, avoiding CQ's relative mesher.

    Copy without triangulations so display meshing cannot modify cached print
    meshes. toCAF(mesh=False) prevents another implicit relative remesh.
    """
    from cadquery.occ_impl.assembly import toCAF
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.Message import Message_ProgressRange
    from OCP.RWGltf import RWGltf_CafWriter
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TColStd import TColStd_IndexedDataMapOfStringString

    display = cq.Assembly(name=assembly.name, loc=assembly.loc)
    for child in assembly.children:
        copy = child.obj.copy(mesh=False)
        mesher = BRepMesh_IncrementalMesh(copy.wrapped, linear_mm, False, angular_rad)
        need(mesher.IsDone(), "Display triangulation failed", "GLB_EXPORT_FAILED", "failed")
        display.add(copy, name=child.name, color=child.color, loc=child.loc)
    display.loc *= cq.Location((0, 0, 0), (1, 0, 0), -90)
    _, doc = toCAF(display, True, False)
    writer = RWGltf_CafWriter(TCollection_AsciiString(str(path)), True)
    need(writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange()),
         "Display GLB writer failed", "GLB_EXPORT_FAILED", "failed")


def build(config: dict, source: Path, out: Path, result: dict) -> list[tuple[str, Path, str | None]]:
    import cadquery as cq
    import OCP

    c = config["request"]["constraints"]
    m, holes, ports = c["manufacturing"], c["mounting_holes"], c["ports"]
    tol, vtol = m["geometry_tolerance_mm"], m["boolean_volume_tolerance_mm3"]
    result["metadata"]["kernel"] = {"name": "CadQuery/OpenCascade", "cadquery_version": cq.__version__, "ocp_version": OCP.__version__}
    progress = config.get("_progress", lambda phase, **details: None)

    def check(name, ok, method, measurements, feature_id=None):
        item = {"name": name, "status": "pass" if ok else "fail", "method": method,
                "tolerance": {"length_mm": tol, "volume_mm3": vtol}, "measurements": measurements}
        if feature_id:
            item["feature_id"] = feature_id
        result["checks"].append(item)
        need(ok, f"CAD check failed: {name}", "GEOMETRY_CHECK_FAILED", "failed",
             feature_id=feature_id, **measurements)

    def box(lo, hi):
        return cq.Solid.makeBox(*(hi[i]-lo[i] for i in range(3)), cq.Vector(*lo))

    def cylinder(x, y, z, radius, height):
        return cq.Solid.makeCylinder(radius, height, cq.Vector(x, y, z))

    def volume(shape):
        need(shape.isValid(), "OpenCascade produced an invalid Boolean shape", "KERNEL_FAILURE", "failed")
        value = shape.Volume()
        need(math.isfinite(value) and value >= -vtol, "Non-finite or negative Boolean volume",
             "KERNEL_FAILURE", "failed")
        return max(0.0, value)

    def valid_solid(shape, name):
        values = {"solid_count": len(shape.Solids()), "volume_mm3": volume(shape),
                  "is_valid": shape.isValid(), "bounds": bbox(shape)}
        check(f"{name}_valid_single_solid", values["is_valid"] and values["solid_count"] == 1
              and values["volume_mm3"] > vtol, "OpenCascade BRepCheck + solid count/volume", values)
        return values

    try:
        progress("importing_step")
        imported = read_step(cq, source)
    except Exception as exc:
        raise CadError("STEP_IMPORT_FAILED", f"Cannot read STEP: {exc}", "failed") from exc
    world_solids = imported.Solids()
    need(bool(world_solids), "STEP contains no solid geometry", "UNSUPPORTED_STEP", "unsupported")
    from OCP.TopoDS import TopoDS_Iterator

    def solid_only(shape):
        if shape.ShapeType() in ("Compound", "CompSolid"):
            children = TopoDS_Iterator(shape.wrapped)
            ok = True
            while children.More():
                ok = solid_only(cq.Shape.cast(children.Value())) and ok
                children.Next()
            return ok
        return shape.ShapeType() == "Solid"

    need(solid_only(imported), "STEP includes free surfaces/edges; solid-only input is required",
         "UNSUPPORTED_STEP", "unsupported")
    # Plane's inverse is a rigid coordinate transform. It does not scale the input.
    plane = cq.Plane(origin=tuple(config["origin"]), xDir=tuple(config["axes"][0]),
                     normal=tuple(config["axes"][2]))
    solids = [s.transformShape(plane.fG) for s in world_solids]
    input_values = []
    progress("validating_input_solids", completed=0, total=len(solids))
    from .source_reuse import reuse_input_validation
    cached = reuse_input_validation(config.get("_source_validation_cache"), config, source, solids,
                                    result, cq.__version__)
    if cached is not None:
        input_values = cached
        progress("reusing_identical_source_validity", completed=len(solids), total=len(solids))
    else:
        for i, s in enumerate(solids):
            input_values.append(valid_solid(s, f"input_solid_{i}"))
            if (i+1) % 16 == 0 or i+1 == len(solids):
                progress("validating_input_solids", completed=i+1, total=len(solids))
    index = config["request"]["board"]["metadata"]["pcb_solid_index"]
    need(index < len(solids), "pcb_solid_index is outside the imported inventory", "PCB_SELECTION_INVALID",
         solid_count=len(solids))
    pcb = solids[index]
    assembly = cq.Compound.makeCompound(solids)
    all_b, pcb_b = bbox(assembly), bbox(pcb)
    pl, ph = pcb_b["min_mm"], pcb_b["max_mm"]
    result["metadata"]["input_solids"] = [
        {"id": f"input_solid_{i}", "index": i, "role": "pcb" if i == index else "unclassified",
         "bounds": values["bounds"], "volume_mm3": values["volume_mm3"]}
        for i, values in enumerate(input_values)]

    def overlapping(a, b):
        # Conservative bounds only select candidates; every remaining pair is
        # tested with the original source solid, never an approximating box.
        return all(a["max_mm"][i] >= b["min_mm"][i] and b["max_mm"][i] >= a["min_mm"][i]
                   for i in range(3))

    def input_intersection(probe, regions=None):
        regions = regions or [bbox(probe)]
        candidates = [i for i, values in enumerate(input_values)
                      if any(overlapping(values["bounds"], region) for region in regions)]
        measured = []
        for i in candidates:
            try:
                common = probe.intersect(solids[i])
                measured.append({"solid_index": i, "volume_mm3": volume(common)})
            except Exception as exc:
                raise CadError("SOURCE_BOOLEAN_FAILED", "Exact intersection with an original source solid failed",
                               "failed", solid_index=i, exception_type=type(exc).__name__) from exc
        return sum(row["volume_mm3"] for row in measured), {
            "source_solid_count": len(solids), "candidate_solid_indices": candidates,
            "per_solid_intersections": measured,
            "volume_measure": "conservative sum of exact per-source-solid common volumes; not a union volume"}
    result["metadata"]["board_placement"] = {
        "frame": c["board_frame"], "output_coordinate_system": "confirmed_board_frame",
        "pcb_solid_index": index, "pcb_bounds": pcb_b, "assembly_bounds": all_b,
        "inference": "Caller-selected solid; no automatic component/connector semantics"}
    geometry_mode = config["request"]["board"]["metadata"].get(
        "pcb_geometry_mode", "rectangular_declared_holes")
    result["metadata"]["board_placement"]["pcb_geometry_mode"] = geometry_mode
    expected_pcb = box(pl, ph)
    progress("checking_original_pcb_geometry", solid_index=index)
    mount = m["pcb_mount"]
    for h in holes:
        x, y, z = h["center_mm"]
        radius = mount["post_diameter_mm"]/2
        need(pl[2]-tol <= z <= ph[2]+tol, "Hole center Z must lie in selected PCB slab", feature_id=h["id"])
        need(pl[0]+radius <= x <= ph[0]-radius and pl[1]+radius <= y <= ph[1]-radius,
             "Confirmed support footprint must fit within the PCB bounding extent", feature_id=h["id"])
        expected_pcb = expected_pcb.cut(cylinder(x, y, pl[2]-tol, h["diameter_mm"]/2, ph[2]-pl[2]+2*tol))
    for i, h in enumerate(holes):
        for other in holes[:i]:
            need(math.dist(h["center_mm"][:2], other["center_mm"][:2]) > mount["post_diameter_mm"]+tol,
                 "PCB supports overlap; confirmed mounting holes cannot be relocated", feature_id=h["id"])
    if geometry_mode == "rectangular_declared_holes":
        mismatch = volume(expected_pcb.cut(pcb)) + volume(pcb.cut(expected_pcb))
        result["checks"].append({"name": "declared_rectangular_pcb_and_holes", "status": "pass" if mismatch <= vtol else "fail",
            "method": "BRep symmetric difference against slab minus all declared through holes",
            "tolerance": {"volume_mm3": vtol}, "measurements": {"symmetric_difference_mm3": mismatch}})
        need(mismatch <= vtol, "Selected PCB is not a rectangular slab with exactly the declared through holes",
             "UNSUPPORTED_PCB_GEOMETRY", "unsupported", symmetric_difference_mm3=mismatch)
    else:
        # Preserve the original contour, slots and nonmount vias. Reconstruct a
        # constant cross-section from the *actual* underside, never a bbox or
        # declared-hole approximation, then prove equality with the full PCB.
        underside = [face for face in pcb.Faces() if face.geomType() == "PLANE"
                     and face.BoundingBox().zlen <= tol
                     and abs(face.BoundingBox().zmin-pl[2]) <= tol]
        need(len(underside) == 1,
             "Original-outline mode requires one planar PCB underside with all original wires",
             "UNSUPPORTED_PCB_GEOMETRY", "unsupported", underside_face_count=len(underside))
        face = underside[0]
        original_extrusion = cq.Solid.extrudeLinear(face.outerWire(), face.innerWires(),
                                                   cq.Vector(0, 0, ph[2]-pl[2]))
        mismatch = volume(original_extrusion.cut(pcb)) + volume(pcb.cut(original_extrusion))
        check("original_pcb_constant_section", mismatch <= vtol,
              "BRep symmetric difference with extrusion of original underside outer and inner wires",
              {"symmetric_difference_mm3": mismatch, "underside_inner_wire_count": len(face.innerWires()),
               "thickness_mm": ph[2]-pl[2]})
        for h in holes:
            x, y, _ = h["center_mm"]
            bore = cylinder(x, y, pl[2], h["diameter_mm"]/2, ph[2]-pl[2])
            bearing = cylinder(x, y, pl[2], mount["post_diameter_mm"]/2, ph[2]-pl[2]).cut(bore)
            bore_material = volume(pcb.intersect(bore))
            missing_support = volume(bearing.cut(pcb))
            check("mounting_hole_bore_void", bore_material <= vtol,
                  "Exact declared through-bore intersection with original PCB solid",
                  {"pcb_material_in_bore_mm3": bore_material}, h["id"])
            check("mounting_support_footprint_within_original_pcb", missing_support <= vtol,
                  "Annular support prism minus original PCB; full original board thickness",
                  {"missing_pcb_material_mm3": missing_support,
                   "support_outer_diameter_mm": mount["post_diameter_mm"],
                   "hole_diameter_mm": h["diameter_mm"]}, h["id"])

    lid_f = m["lid_fastener"]
    progress("constructing_base_lid_and_supports")
    r = lid_f["post_diameter_mm"]/2
    wall, bottom, top = m["wall_thickness_mm"], m["base_thickness_mm"], m["lid_thickness_mm"]
    gap = m["clearance_xy_mm"]
    compact = m.get("enclosure_fit", "legacy") == "compact"
    display = c.get("display")
    from .compact import body_and_footprint_budget
    budget = body_and_footprint_budget([row["bounds"] for row in input_values], index, ports, m, display=display)
    fitted_lo, fitted_hi = budget["fitted_component_bounds"]["min_mm"], budget["fitted_component_bounds"]["max_mm"]
    inner_lo, inner_hi = budget["cavity_bounds"]["min_mm"][:2], budget["cavity_bounds"]["max_mm"][:2]
    floor_z, lid_z = budget["cavity_bounds"]["min_mm"][2], budget["lid_mating_plane_z_mm"]
    outer_lo, outer_hi = budget["body_outer_bounds"]["min_mm"], budget["body_outer_bounds"]["max_mm"]
    display_meta = budget["display"]
    display_shape = box(display_meta["envelope_bounds"]["min_mm"], display_meta["envelope_bounds"]["max_mm"]) if display else None
    removable = bool(display and display.get("support_structure") == "removable_pcb_carrier")
    carrier = None
    carrier_bottom = pl[2]-display["pcb_post_height_mm"]-display["pcb_bridge_thickness_mm"] if removable else None
    carrier_top = pl[2]-display["pcb_post_height_mm"] if removable else None
    if removable:
        clearance = display["carrier_clearance_xy_mm"]
        frame_width = display["carrier_frame_width_mm"]
        carrier_xy_lo = [inner_lo[i]+clearance for i in (0, 1)]
        carrier_xy_hi = [inner_hi[i]-clearance for i in (0, 1)]
        need(all(carrier_xy_hi[i]-carrier_xy_lo[i] > 2*frame_width for i in (0, 1)),
             "Carrier frame does not leave a positive opening")
        carrier = box([*carrier_xy_lo, carrier_bottom], [*carrier_xy_hi, carrier_top]).cut(
            box([carrier_xy_lo[0]+frame_width, carrier_xy_lo[1]+frame_width, carrier_bottom-tol],
                [carrier_xy_hi[0]-frame_width, carrier_xy_hi[1]-frame_width, carrier_top+tol]))
        display_meta.update(support_structure="removable_pcb_carrier", carrier_access_slot_bounds=[],
                            carrier_clamp_screw_centers_mm=[], carrier_contact_planes_mm={
                                "base_to_carrier": carrier_bottom, "carrier_to_lid": carrier_top,
                                "carrier_to_pcb": pl[2]})
    need(all_b["min_mm"][2]-floor_z >= m["clearance_bottom_mm"],
         "Base floor does not provide required clearance below the imported assembly")
    need(lid_f["pilot_depth_mm"] < lid_z-floor_z-tol, "Lid pilot must remain blind above base floor")
    reserve = 0 if compact else 2*r
    base = box(outer_lo, [*outer_hi[:2], lid_z]).cut(box([*inner_lo, floor_z], [*inner_hi, lid_z+tol]))
    lid = box([*outer_lo[:2], lid_z], outer_hi)
    # The base remains a subset of these positive CSG material regions because
    # all later operations only add the recorded posts or remove material.
    # This prevents its overall cavity bbox from selecting every component.
    def region(lo, hi):
        return {"min_mm": [v-tol for v in lo], "max_mm": [v+tol for v in hi]}
    base_regions = [region(outer_lo, [*outer_hi[:2], floor_z]),
                    region([outer_lo[0], outer_lo[1], floor_z], [inner_lo[0], outer_hi[1], lid_z]),
                    region([inner_hi[0], outer_lo[1], floor_z], [outer_hi[0], outer_hi[1], lid_z]),
                    region([inner_lo[0], outer_lo[1], floor_z], [inner_hi[0], inner_lo[1], lid_z]),
                    region([inner_lo[0], inner_hi[1], floor_z], [inner_hi[0], outer_hi[1], lid_z])]
    def port_cutter(p, extension=0):
        axis = "xyz".index(p["face"][0])
        tangents = [i for i in range(3) if i != axis]
        center = p["center_mm"]
        is_min = p["face"].endswith("min")
        end = outer_lo[axis]-tol-extension if is_min else outer_hi[axis]+tol+extension
        interior = ([*inner_lo, floor_z] if is_min else [*inner_hi, lid_z])[axis]
        opposite = (max(center[axis], interior) if is_min else min(center[axis], interior)) if compact else center[axis]
        access = p.get("surface_access", {})
        if compact and axis in (0, 1):
            sign = -1 if is_min else 1
            # Adjacent U bays may merge; every unchanged opening must pierce
            # the deepest local return wall, not only its own front panel.
            panel_planes = [other["center_mm"][axis]+sign*surface_offset_mm(other["surface_access"])
                            -sign*other["surface_access"]["panel_thickness_mm"]
                            for other in ports if other.get("face") == p["face"]
                            and other.get("surface_access", {}).get("mode") == "side_u_bay"]
            if panel_planes:
                opposite = max(opposite, max(panel_planes)+tol) if is_min else min(opposite, min(panel_planes)-tol)
        if compact and axis == 2 and not is_min and "panel_thickness_mm" in access:
            opposite = min(opposite, center[axis]+surface_offset_mm(access)-access["panel_thickness_mm"]-tol)
        start, stop = min(opposite, end), max(opposite, end)
        if p["shape"] == "rectangle":
            lo, hi = list(center), list(center)
            lo[axis], hi[axis] = start, stop
            for j, dim in zip(tangents, (p["width_mm"], p["height_mm"])):
                lo[j], hi[j] = center[j]-dim/2, center[j]+dim/2
            return box(lo, hi)
        origin = list(center)
        origin[axis] = start
        direction = [int(i == axis) for i in range(3)]
        return cq.Solid.makeCylinder(p["diameter_mm"]/2, stop-start, cq.Vector(*origin), cq.Vector(*direction))

    active_ports = [p for p in ports if p["access"] in ("external", "maintenance")]
    preliminary_cutters = [port_cutter(p, 2*r if compact else 0) for p in active_ports]
    preliminary_cutters += [box(k["min_mm"], k["max_mm"]) for k in c["keepouts"]]
    preliminary_cutters += [box(k["min_mm"], k["max_mm"]) for k in c.get("open_operation_regions", [])]
    display_cutters = []
    if display:
        screen_lo, screen_hi = display_meta["envelope_bounds"]["min_mm"], display_meta["envelope_bounds"]["max_mm"]
        need(pl[2]-all_b["min_mm"][2]+display["clearance_z_mm"] <= display["board_to_display_gap_mm"]+tol,
             "Screen top does not clear the retained original underside components", "DISPLAY_SOURCE_CONFLICT", "failed")
        ledge = display["face_opening"]["ledge_width_mm"]
        window_lo = [screen_lo[0]+ledge, screen_lo[1]+ledge, outer_lo[2]-tol]
        window_hi = [screen_hi[0]-ledge, screen_hi[1]-ledge, screen_lo[2]+tol]
        display_meta["face_opening_bounds"] = {"min_mm": window_lo, "max_mm": window_hi,
                                               "active_area_verified": False}
        ledge_outer = box([*screen_lo[:2], outer_lo[2]], [*screen_hi[:2], screen_lo[2]])
        window = box(window_lo, window_hi)
        ledge_shape = ledge_outer.cut(window)
        base = base.fuse(ledge_shape)
        base_regions.append(bbox(ledge_shape))
        display_meta["edge_ledge_bounds"] = [bbox(ledge_shape)]
        display_cutters.append((display["id"]+"-front-window", window))
        channel = display["ribbon_channel"]
        axis = "xy".index(channel["face"][0]); tangent = 1-axis
        outward_min = channel["face"].endswith("min")
        channel_lo, channel_hi = list(outer_lo), list(outer_hi)
        channel_lo[axis] = outer_lo[axis]-tol if outward_min else ph[axis]-wall
        channel_hi[axis] = pl[axis]+wall if outward_min else outer_hi[axis]+tol
        channel_lo[tangent] = channel["center_tangent_mm"]-channel["width_mm"]/2
        channel_hi[tangent] = channel["center_tangent_mm"]+channel["width_mm"]/2
        channel_lo[2] = screen_hi[2]+display["clearance_z_mm"]
        channel_hi[2] = channel_lo[2]+channel["height_mm"]
        ribbon = box(channel_lo, channel_hi)
        display_meta["ribbon_channel_bounds"] = {"min_mm": channel_lo, "max_mm": channel_hi,
                                                   "source": channel["source"], "scope": "static straight box only"}
        display_cutters.append((display["id"]+"-ribbon-channel", ribbon))
        preliminary_cutters += [item[1] for item in display_cutters]
    surface_access = []
    side_bay_pockets = []
    side_bay_seats = []
    side_bay_evidence = []
    for p in active_ports:
        axis = "xyz".index(p["face"][0])
        sign = -1 if p["face"].endswith("min") else 1
        global_plane = (outer_lo if sign < 0 else outer_hi)[axis]
        local_plane = global_plane
        access = p.get("surface_access")
        recessed_panel = False
        if compact and access and access.get("mode") == "side_u_bay":
            from .side_bay import side_bay
            pocket, positive, seat, evidence = side_bay(cq, p, m, {"min_mm": outer_lo, "max_mm": outer_hi}, lid_z, ports)
            base, lid = base.cut(pocket).cut(seat), lid.cut(pocket).fuse(positive)
            preliminary_cutters.extend([pocket, seat])
            side_bay_pockets.append((p["id"], pocket))
            side_bay_seats.append((p["id"], seat))
            side_bay_evidence.append(evidence)
            local_plane, recessed_panel = evidence["local_panel_outer_plane_mm"], True
        elif compact and access and "panel_thickness_mm" in access:
            need(axis == 2 and sign == 1, "Local recessed panels currently support top-face openings only",
                 "UNSUPPORTED_RECESSED_PANEL", "unsupported", feature_id=p["id"])
            need(p["shape"] == "rectangle", "Local recessed panels require a rectangular access opening",
                 "UNSUPPORTED_RECESSED_PANEL", "unsupported", feature_id=p["id"])
            target = p["center_mm"][axis]+surface_offset_mm(access)
            if target < global_plane-tol:
                margin, panel = access["recess_margin_mm"], access["panel_thickness_mm"]
                need(panel > 10*tol and margin >= wall,
                     "Recess panel must exceed ten tolerances and margin must preserve wall thickness", feature_id=p["id"])
                x, y = p["center_mm"][:2]
                wx, wy = p["width_mm"]/2+margin, p["height_mm"]/2+margin
                pocket_lo, pocket_hi = [x-wx, y-wy, target], [x+wx, y+wy, global_plane+tol]
                tube_lo, tube_hi = [x-wx-wall, y-wy-wall, target-panel], [x+wx+wall, y+wy+wall, global_plane]
                pocket = box(pocket_lo, pocket_hi)
                tube = box(tube_lo, tube_hi).cut(pocket)
                # A depressed annular panel and its vertical return walls are
                # actual BRep material, not an opening-only access assertion.
                lid = lid.cut(pocket).fuse(tube)
                local_plane, recessed_panel = target, True
                preliminary_cutters.append(pocket)
        signed = sign*(p["center_mm"][axis]-local_plane)
        surface_access.append({"feature_id": p["id"], "face": p["face"],
            "mating_plane_mm": p["center_mm"][axis], "body_outer_plane_mm": global_plane,
            "local_panel_outer_plane_mm": local_plane, "signed_protrusion_mm": signed,
            "recess_mm": max(0.0, -signed), "local_recessed_panel": recessed_panel,
            "max_recess_mm": access.get("max_recess_mm") if access else None,
            "min_protrusion_mm": access.get("min_protrusion_mm") if access else None,
            "target_met_by_construction": signed >= -surface_offset_mm(access)-tol if access else None,
            "evidence_scope": "construction planes only; independent adjacent-shell BRep measurement required"})
    protected = []
    contacts = []
    for h in holes:
        x, y, _ = h["center_mm"]
        post_bottom = pl[2]-display["pcb_post_height_mm"] if display else floor_z
        post = cylinder(x, y, post_bottom-tol, mount["post_diameter_mm"]/2, pl[2]-post_bottom+tol)
        bridge = None
        if display:
            # Short supports are suspended from an outer wall, above the
            # screen. They never continue through the display package.
            choices = [(abs(value-coordinate), axis, is_min) for axis, coordinate in enumerate((x, y))
                       for is_min, value in ((True, carrier_xy_lo[axis] if removable else inner_lo[axis]),
                                            (False, carrier_xy_hi[axis] if removable else inner_hi[axis]))]
            _, normal, is_min = min(choices)
            tangent = 1-normal
            lo = [x-mount["post_diameter_mm"]/2, y-mount["post_diameter_mm"]/2,
                  post_bottom-display["pcb_bridge_thickness_mm"]]
            hi = [x+mount["post_diameter_mm"]/2, y+mount["post_diameter_mm"]/2, post_bottom]
            if is_min:
                lo[normal] = carrier_xy_lo[normal] if removable else outer_lo[normal]
            else:
                hi[normal] = carrier_xy_hi[normal] if removable else outer_hi[normal]
            bridge = box(lo, hi)
            if removable:
                carrier = carrier.fuse(bridge)
            else:
                base = base.fuse(bridge)
            base_regions.append(bbox(bridge))
            display_meta["pcb_bridge_bounds"].append({"feature_id": h["id"], **bbox(bridge)})
            display_meta["pcb_post_bounds"].append({"feature_id": h["id"], **bbox(post)})
        base_regions.append(bbox(post))
        pilot = cylinder(x, y, pl[2]-mount["pilot_depth_mm"], mount["pilot_diameter_mm"]/2,
                         mount["pilot_depth_mm"]+tol)
        if removable:
            carrier = carrier.fuse(post).cut(pilot)
        else:
            base = base.fuse(post).cut(pilot)
        anchor = (post.fuse(bridge).cut(pilot) if display else
                  cylinder(x, y, outer_lo[2], mount["post_diameter_mm"]/2, pl[2]-outer_lo[2]).cut(pilot))
        protected.append(("pcb_carrier" if removable else "base", h["id"], anchor))
        contacts.append({"feature_id": h["id"], "parts": ["pcb_carrier" if removable else "base", f"input_solid_{index}"],
                         "kind": "pcb_standoff_annular_face", "center_mm": [x, y, pl[2]],
                         "normal": [0, 0, 1], "outer_diameter_mm": mount["post_diameter_mm"],
                         "inner_diameter_mm": h["diameter_mm"]})
    overlap = min(wall/2, r/4)
    need(lid_f["head_diameter_mm"]/2 <= r-overlap,
         "Head counterbore would leave less than wall thickness at a lid corner")
    screw_centers = [[x, y, lid_z]
        for x in (inner_lo[0]+r-overlap, inner_hi[0]-r+overlap)
        for y in (inner_lo[1]+r-overlap, inner_hi[1]-r+overlap)]
    compact_ear_axis = None
    if compact:
        # Ears touch the cavity boundary but never enter its required XY gap.
        # Search deterministic corner-near slots along each pair of sides;
        # exact source/cutter Booleans exclude conflict-prone locations.
        layouts = []
        for normal in (0, 1):
            if lid_f.get("preferred_ear_axis") is not None and normal != "xy".index(lid_f["preferred_ear_axis"]):
                continue
            tangent = 1-normal
            start, stop = inner_lo[tangent]+r, inner_hi[tangent]-r
            if stop-start < 2*r+tol:
                continue
            slots = [start+(stop-start)*i/24 for i in range(25)]
            selected = []
            feasible = True
            for side in (0, 1):
                nvalue = inner_lo[normal]-r if side == 0 else inner_hi[normal]+r
                used = []
                for reverse in (False, True):
                    found = None
                    for tvalue in (reversed(slots) if reverse else slots):
                        if any(abs(tvalue-other) <= 2*r+tol for other in used):
                            continue
                        xy = [0.0, 0.0]
                        xy[normal], xy[tangent] = nvalue, tvalue
                        candidate = cylinder(*xy, outer_lo[2], r, outer_hi[2]-outer_lo[2])
                        if any(overlapping(bbox(candidate), bbox(cut)) and volume(candidate.intersect(cut)) > vtol
                               for cut in preliminary_cutters):
                            continue
                        conflict, _ = input_intersection(candidate)
                        if conflict > vtol:
                            continue
                        found = [*xy, lid_z]
                        used.append(tvalue)
                        break
                    if found is None:
                        feasible = False
                        break
                    selected.append(found)
                if not feasible:
                    break
            if feasible:
                extents = [outer_hi[i]-outer_lo[i]+(4*r-2*wall if i == normal else 0) for i in (0, 1)]
                layouts.append((extents[0]*extents[1], normal, selected))
        need(bool(layouts), "No compact lid-fastener layout preserves every required opening and source solid",
             "COMPACT_FASTENER_LAYOUT_CONFLICT", "failed")
        _, compact_ear_axis, screw_centers = min(layouts, key=lambda item: (item[0], item[1]))
    for i, (x, y, _) in enumerate(screw_centers):
        if removable:
            # The same M3 axis clamps lid spacer -> carrier ear -> base pilot.
            # Only the base's low post is present while inserting the screen.
            post_top = carrier_bottom
            need(lid_f["pilot_depth_mm"] < post_top-outer_lo[2]-tol,
                 "Carrier clamp pilot must remain blind inside the low base ear")
            pilot = cylinder(x, y, post_top-lid_f["pilot_depth_mm"], lid_f["pilot_diameter_mm"]/2,
                             lid_f["pilot_depth_mm"]+tol)
            base_ear = cylinder(x, y, outer_lo[2], r, post_top-outer_lo[2]).cut(pilot)
            base = base.fuse(base_ear)
            base_regions.append(bbox(base_ear))
            carrier_clearance = cylinder(x, y, carrier_bottom-tol, lid_f["clearance_diameter_mm"]/2,
                                         carrier_top-carrier_bottom+2*tol)
            carrier_ear = cylinder(x, y, carrier_bottom, r, carrier_top-carrier_bottom).cut(carrier_clearance)
            normal, tangent = compact_ear_axis, 1-compact_ear_axis
            is_min = (x, y)[normal] < inner_lo[normal]
            arm_lo, arm_hi = [x-r, y-r, carrier_bottom], [x+r, y+r, carrier_top]
            arm_lo[tangent] = max(arm_lo[tangent], carrier_xy_lo[tangent])
            arm_hi[tangent] = min(arm_hi[tangent], carrier_xy_hi[tangent])
            need(arm_hi[tangent]-(x, y)[tangent] >= lid_f["clearance_diameter_mm"]/2+wall
                 and (x, y)[tangent]-arm_lo[tangent] >= lid_f["clearance_diameter_mm"]/2+wall,
                 "Carrier sliding-arm ligament is below the declared wall thickness")
            if is_min:
                arm_lo[normal] = (x, y)[normal]
                arm_hi[normal] = carrier_xy_lo[normal]+frame_width
            else:
                arm_lo[normal] = carrier_xy_hi[normal]-frame_width
                arm_hi[normal] = (x, y)[normal]
            arm = box(arm_lo, arm_hi).cut(carrier_clearance)
            carrier = carrier.fuse(arm).fuse(carrier_ear)
            slot_gap = display["carrier_slot_clearance_mm"]
            slot_lo, slot_hi = list(arm_lo), list(arm_hi)
            slot_lo[tangent] -= slot_gap
            slot_hi[tangent] += slot_gap
            slot_lo[normal] = outer_lo[normal]-slot_gap if is_min else inner_hi[normal]-slot_gap
            slot_hi[normal] = inner_lo[normal]+slot_gap if is_min else outer_hi[normal]+slot_gap
            slot_lo[2], slot_hi[2] = carrier_bottom, lid_z+tol
            slot = box(slot_lo, slot_hi)
            base = base.cut(slot)
            display_meta["carrier_access_slot_bounds"].append({"feature_id": f"carrier-clamp-{i}", **bbox(slot)})
            # Hanging tongue fills the upper wall slot while retaining the
            # declared sliding gap; its lower edge never crushes the arm.
            fill_lo, fill_hi = list(slot_lo), list(slot_hi)
            fill_lo[tangent] += slot_gap
            fill_hi[tangent] -= slot_gap
            fill_lo[normal] = outer_lo[normal] if is_min else inner_hi[normal]
            fill_hi[normal] = inner_lo[normal] if is_min else outer_hi[normal]
            fill_lo[2], fill_hi[2] = carrier_top+slot_gap, lid_z+tol
            lid = lid.fuse(box(fill_lo, fill_hi))
            clearance = cylinder(x, y, carrier_top-tol, lid_f["clearance_diameter_mm"]/2,
                                 outer_hi[2]-carrier_top+2*tol)
            counterbore = cylinder(x, y, outer_hi[2]-lid_f["head_depth_mm"],
                                   lid_f["head_diameter_mm"]/2, lid_f["head_depth_mm"]+tol)
            stem = cylinder(x, y, carrier_top, r, outer_hi[2]-carrier_top)
            lid = lid.fuse(stem).cut(clearance).cut(counterbore)
            protected.extend([("base", f"lid_screw_{i}", base_ear), ("pcb_carrier", f"lid_screw_{i}", carrier_ear),
                              ("pcb_carrier", f"carrier_arm_{i}", arm),
                              ("lid", f"lid_screw_{i}", stem.cut(clearance).cut(counterbore))])
            display_meta["carrier_clamp_screw_centers_mm"].append([x, y, carrier_bottom])
            for pair, z in ((["base", "pcb_carrier"], carrier_bottom), (["pcb_carrier", "lid"], carrier_top)):
                contacts.append({"feature_id": f"carrier_clamp_{i}", "parts": pair,
                                 "kind": "annular_m3_clamp_seating" if display.get("carrier_clamp") == "shared_lid_m3" else "annular_fastener_clamp_seating", "center_mm": [x, y, z],
                                 "normal": [0, 0, 1], "outer_diameter_mm": 2*r,
                                 "inner_diameter_mm": lid_f["clearance_diameter_mm"]})
        else:
            post = cylinder(x, y, floor_z-tol, r, lid_z-floor_z+tol)
            base_regions.append(bbox(post))
            pilot = cylinder(x, y, lid_z-lid_f["pilot_depth_mm"], lid_f["pilot_diameter_mm"]/2,
                             lid_f["pilot_depth_mm"]+tol)
            clearance = cylinder(x, y, lid_z-tol, lid_f["clearance_diameter_mm"]/2, top+2*tol)
            counterbore = cylinder(x, y, lid_z+top-lid_f["head_depth_mm"], lid_f["head_diameter_mm"]/2,
                               lid_f["head_depth_mm"]+tol)
            if compact:
                # Full-height local ear joins the existing wall by its circular
                # segment, without reserving that diameter around the whole PCB.
                ear = cylinder(x, y, outer_lo[2], r, lid_z-outer_lo[2])
                base_regions.append(bbox(ear))
                base = base.fuse(ear)
                lid = lid.fuse(cylinder(x, y, lid_z, r, top))
            base = base.fuse(post).cut(pilot)
            lid = lid.cut(clearance).cut(counterbore)
            anchor = cylinder(x, y, outer_lo[2], r, lid_z-outer_lo[2]).cut(pilot)
            lid_ring = cylinder(x, y, lid_z, r, top).cut(clearance).cut(counterbore)
            protected.extend([("base", f"lid_screw_{i}", anchor), ("lid", f"lid_screw_{i}", lid_ring)])
    contacts.append({"parts": ["base", "lid"], "kind": "planar_rim_and_screw_post_contact",
                     "plane_z_mm": lid_z, "normal": [0, 0, 1]})
    cutters = list(display_cutters)
    service_volumes = []
    for p in ports:
        if p["access"] in ("internal", "covered"):
            continue
        progress("constructing_required_openings", feature_id=p["id"])
        axis = "xyz".index(p["face"][0])
        tangents = [i for i in range(3) if i != axis]
        center = p["center_mm"]
        is_min = p["face"].endswith("min")
        need(all_b["min_mm"][axis]-tol <= center[axis] <= all_b["max_mm"][axis]+tol,
             "Opening mating plane must lie within the imported assembly envelope", feature_id=p["id"])
        cutter = port_cutter(p, 2*r if compact else 0)
        service = p["service_envelope"]
        slo, shi = service["min_mm"], service["max_mm"]
        need((slo[axis] <= outer_lo[axis]-tol if is_min else shi[axis] >= outer_hi[axis]+tol),
             "Service envelope must reach beyond the enclosure exterior", feature_id=p["id"])
        cut_volume = volume(base.intersect(cutter)) + volume(lid.intersect(cutter))
        need(cut_volume > vtol, "Opening does not intersect the case", "OPENING_MISSES_CASE", feature_id=p["id"])
        cutters.append((p["id"], cutter))
        service_volumes.append((p["id"], box(slo, shi)))
    for k in [*c["keepouts"], *c.get("open_operation_regions", [])]:
        cutters.append((k["id"], box(k["min_mm"], k["max_mm"])))
    for fid, cutter in cutters:
        progress("cutting_and_protecting_features", feature_id=fid)
        for _, pid, material in protected:
            overlap_volume = volume(material.intersect(cutter))
            need(overlap_volume <= vtol, "Opening/keepout would remove required support or fastener material",
                 "PROTECTED_FEATURE_CONFLICT", "failed", feature_id=fid,
                 protected_feature_id=pid, intersection_mm3=overlap_volume)
        base, lid = base.cut(cutter), lid.cut(cutter)
        if removable:
            carrier = carrier.cut(cutter)
    for fid, pocket in side_bay_pockets:
        # This pocket only removes base/lid material; the separate carrier
        # is untouched and retains the original mandatory service checks.
        for owner, pid, material in protected:
            if owner not in ("base", "lid"):
                continue
            hit = volume(material.intersect(pocket))
            need(hit <= vtol, "Side bay pocket would remove protected support or clamp material",
                 "PROTECTED_FEATURE_CONFLICT", "failed", feature_id=fid,
                 protected_feature_id=pid, intersection_mm3=hit)
        base, lid = base.cut(pocket), lid.cut(pocket)
        hit = volume(base.intersect(pocket))+volume(lid.intersect(pocket))
        check("side_bay_shell_pocket_empty", hit <= vtol,
              "Exact BRep Common with explicit base/lid-only side bay pocket",
              {"remaining_shell_material_mm3": hit, "scope": "base/lid; original plug service envelope still applies to carrier/screen"}, fid)
    for fid, seat in side_bay_seats:
        for owner, pid, material in protected:
            if owner != "base":
                continue
            hit = volume(material.intersect(seat))
            need(hit <= vtol, "Side bay receiving slot would remove protected support or clamp material",
                 "PROTECTED_FEATURE_CONFLICT", "failed", feature_id=fid,
                 protected_feature_id=pid, intersection_mm3=hit)
        base = base.cut(seat)
    base, lid = base.clean(), lid.clean()
    valid_solid(base, "base")
    valid_solid(lid, "lid")
    case_parts = [("base", base), ("lid", lid)]
    if removable:
        carrier = carrier.clean()
        valid_solid(carrier, "pcb_carrier")
        case_parts.append(("pcb_carrier", carrier))
        display_meta["carrier_bounds"] = bbox(carrier)
    artifacts = []
    def export_checked_step(name, shape):
        progress("exporting_and_reimporting_step", part=name)
        step_file = out / f"{name}.step"
        cq.exporters.export(shape, str(step_file), exportType="STEP")
        reread = read_step(cq, step_file)
        delta = abs(volume(reread)-volume(shape))
        check(f"{name}_step_roundtrip", reread.isValid() and len(reread.Solids()) == len(shape.Solids()) and delta <= vtol,
              "Re-import exported STEP; BRepCheck, solid count and volume comparison", {"volume_delta_mm3": delta})
        artifacts.append((f"{name}_step", step_file, "mm"))
    # Run-local early geometry is diagnostic until all remaining checks pass.
    # Keeping it aids exact reproduction of a later kernel Boolean failure.
    export_checked_step("base", base)
    export_checked_step("lid", lid)
    if removable:
        export_checked_step("pcb_carrier", carrier)
    for fid, shape in cutters:
        remaining = sum(volume(part.intersect(shape)) for _, part in case_parts)
        check("required_opening_or_keepout_empty", remaining <= vtol,
              "BRep Boolean intersection with exact requested cutter", {"remaining_material_mm3": remaining}, fid)
    for fid, shape in service_volumes:
        progress("checking_service_volume", feature_id=fid)
        remaining = 0.0
        for name, part in case_parts:
            progress("checking_service_case_volume", feature_id=fid, part=name)
            try:
                remaining += volume(part.intersect(shape))
            except Exception as exc:
                raise CadError("CASE_SERVICE_BOOLEAN_FAILED", "Exact case/service intersection failed", "failed",
                               feature_id=fid, part=name, exception_type=type(exc).__name__) from exc
        interference, source_evidence = input_intersection(shape)
        check("service_envelope_empty", remaining <= vtol and interference <= vtol,
              "BRep Boolean intersection with caller-provided service box", {
                  "remaining_case_material_mm3": remaining, "input_intersection_mm3": interference,
                  **source_evidence}, fid)
    for name, shape in case_parts:
        progress("checking_case_component_nonpenetration", part=name)
        intersection, source_evidence = input_intersection(shape, base_regions if name == "base" else None)
        check(f"{name}_input_nonpenetration", intersection <= vtol,
              "Conservative material-bound screening and exact per-source-solid Common at final pose",
              {"intersection_mm3": intersection, **source_evidence})
    if display:
        screen_source_intersection, source_evidence = input_intersection(display_shape)
        check("display_source_nonpenetration", screen_source_intersection <= vtol,
              "Exact per-original-source-solid Common with declared screen envelope",
              {"intersection_mm3": screen_source_intersection, **source_evidence})
        for name, shape in case_parts:
            hit = volume(shape.intersect(display_shape))
            check(f"display_{name}_nonpenetration", hit <= vtol,
                  "Exact BRep Common with user-dimension display envelope; edge face contact allowed",
                  {"intersection_mm3": hit})
        ribbon_meta = display_meta["ribbon_channel_bounds"]
        ribbon = box(ribbon_meta["min_mm"], ribbon_meta["max_mm"])
        hit, source_evidence = input_intersection(ribbon)
        check("ribbon_static_source_nonpenetration", hit <= vtol,
              "Exact per-source-solid Common with declared static ribbon channel",
              {"intersection_mm3": hit, **source_evidence})
    check("base_lid_nonpenetration", volume(base.intersect(lid)) <= vtol,
          "BRep Boolean common volume at final assembled pose; planar rim contact allowed",
          {"intersection_mm3": volume(base.intersect(lid))})
    if removable:
        for other_name, other_shape in (("base", base), ("lid", lid)):
            hit = volume(carrier.intersect(other_shape))
            check(f"pcb_carrier_{other_name}_nonpenetration", hit <= vtol,
                  "Exact BRep Common; declared annular clamp plane contact allowed", {"intersection_mm3": hit})
    actual_bounds = bbox(cq.Compound.makeCompound([part for _, part in case_parts]))
    # Freeze authoritative geometry measurements before any STL mesher can
    # populate cached triangulations used by OCC's display bbox calculation.
    part_geometry_metadata = [{"id": name, "bounds": bbox(shape), "volume_mm3": volume(shape)}
                              for name, shape in case_parts]
    placements = {}
    for name, shape in case_parts:
        z = outer_lo[2] if name == "base" else (lid_z if name == "lid" else carrier_bottom)
        progress("exporting_print_stl", part=name)
        # Compact lids print with the broad external datum down; a depressed
        # panel must not leave the rest of the lid floating above the bed.
        flip = compact and name == "lid"
        printable = shape.rotate((0, 0, 0), (1, 0, 0), 180) if flip else shape
        part_bounds = bbox(printable)
        shift = [-part_bounds["min_mm"][0], -part_bounds["min_mm"][1], -part_bounds["min_mm"][2]] if compact else [-outer_lo[0], -outer_lo[1], -z]
        stl = out / f"{name}.stl"
        cq.exporters.export(printable.translate(tuple(shift)), str(stl), exportType="STL",
                            tolerance=m["mesh_linear_tolerance_mm"], angularTolerance=m["mesh_angular_tolerance_rad"])
        artifacts.append((f"{name}_stl", stl, "mm"))
        placements[name] = {"translation_mm": shift, "rotation": "180deg_about_x" if flip else "identity", "bed_plane_z_mm": 0}
        if flip:
            placements[name]["rotation_matrix"] = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
            placements[name]["operation_order"] = "rotate_about_origin_then_translate"
    assy = cq.Assembly(name="justbuild")
    assy.add(base, name="base", color=cq.Color(0.7, 0.72, 0.8, 0.55))
    assy.add(lid, name="lid", color=cq.Color(0.4, 0.65, 0.9, 0.4))
    if removable:
        assy.add(carrier, name="pcb_carrier", color=cq.Color(0.95, 0.65, 0.2))
    if display:
        assy.add(display_shape, name="screen-envelope_user-dimensions", color=cq.Color(0.1, 0.2, 0.3, 0.8))
        export_checked_step("screen_envelope", display_shape)
    for i, s in enumerate(solids):
        assy.add(s, name=f"input_solid_{i}", color=cq.Color(0.1, 0.5, 0.2) if i == index else cq.Color(0.6, 0.6, 0.6))
    assembly_file = out / "assembly.step"
    # CadQuery's glTF exporter maps the CAD Z-up/mm frame to glTF Y-up/meters.
    glb = out / "review.glb"
    progress("exporting_display_glb")
    review_linear = m.get("review_mesh_linear_tolerance_mm", m["mesh_linear_tolerance_mm"])
    review_angular = m.get("review_mesh_angular_tolerance_rad", m["mesh_angular_tolerance_rad"])
    result["metadata"]["display_mesh"] = {
        "artifact_kind": "review_glb", "linear_tolerance_mm": review_linear,
        "angular_tolerance_rad": review_angular, "authoritative_geometry": False,
        "stl_linear_tolerance_mm": m["mesh_linear_tolerance_mm"],
        "stl_angular_tolerance_rad": m["mesh_angular_tolerance_rad"],
        "linear_deflection_mode": ("absolute_mm" if any(k in m for k in (
            "review_mesh_linear_tolerance_mm", "review_mesh_angular_tolerance_rad"))
            else "cadquery_relative_legacy"),
    }
    try:
        if result["metadata"]["display_mesh"]["linear_deflection_mode"] == "absolute_mm":
            export_glb_absolute(cq, assy, glb, review_linear, review_angular)
        else:
            assy.export(str(glb), "GLTF", tolerance=review_linear,
                        angularTolerance=review_angular)
        scale_glb_to_meters(glb)
        artifacts.append(("review_glb", glb, "m"))
    except Exception as exc:
        result["issues"].append({"code": "REVIEW_GLB_UNAVAILABLE", "severity": "warning",
                                 "message": str(exc), "evidence": {}})
    result["metadata"].update({
        "generated_configuration": m, "outer_bounds": actual_bounds if compact else {"min_mm": outer_lo, "max_mm": outer_hi},
        "body_outer_bounds": {"min_mm": outer_lo, "max_mm": outer_hi},
        "shell_wall_planes": {f"{letter}_{side}": (outer_lo if side == "min" else outer_hi)[i]
                              for i, letter in enumerate("xyz") for side in ("min", "max")},
        "actual_footprint_mm": [actual_bounds["max_mm"][i]-actual_bounds["min_mm"][i] for i in (0, 1)],
        "enclosure_fit": {"mode": "compact" if compact else "legacy", "local_ear_axis": compact_ear_axis,
                          "fitted_component_bounds": {"min_mm": fitted_lo, "max_mm": fitted_hi},
                          "whole_side_fastener_reserve_mm": reserve},
        "port_surface_access": surface_access,
        "side_bays": side_bay_evidence,
        "display": display_meta,
        "open_operation_regions": c.get("open_operation_regions", []),
        "cavity_bounds": {"min_mm": [*inner_lo, floor_z], "max_mm": [*inner_hi, lid_z]},
        "parts": part_geometry_metadata,
        "lid_screw_centers_mm": screw_centers, "corner_post_wall_overlap_mm": overlap,
        "intentional_contacts": contacts, "ports": ports, "keepouts": c["keepouts"], "mounting_holes": holes,
        "stl_placements": placements,
        "review_glb_frame": {"units": "m", "mapping_from_board_mm": "[x, z, -y] * 0.001"},
        "hardware": {"modeled": False, "pcb_mount": mount, "lid_fastener": lid_f,
                     "pcb_mount_part": "pcb_carrier" if removable else "base",
                     "carrier_clamp": {"type": display.get("carrier_clamp", "shared_lid_m3"), "base_pilot_entry_z_mm": carrier_bottom,
                                       "carrier_top_z_mm": carrier_top, "prototype_screw_length_mm": lid_f.get("screw_length_mm", 16.0),
                                       "screw_length_fit_verified": False} if removable else None},
    })
    if config["request"]["board"]["metadata"].get("source_export_mode") == "native_identity_bytes":
        from .source_reuse import export_identity_source
        progress("preserving_native_identity_source_bytes")
        export_identity_source(config, source, out, result, artifacts)
        return artifacts
    export_checked_step("normalized_input", assembly)
    progress("exporting_named_assembly_step")
    assy.export(str(assembly_file), "STEP")
    assembled = read_step(cq, assembly_file)
    check("assembly_step_roundtrip", assembled.isValid() and len(assembled.Solids()) == len(solids)+2+(1 if display else 0)+(1 if removable else 0),
          "Re-import named STEP assembly; BRepCheck and solid count", {"solid_count": len(assembled.Solids())})
    artifacts.append(("assembly_step", assembly_file, "mm"))
    return artifacts
