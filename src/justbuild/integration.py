"""Translate the reviewed CAD dialect into the independent checker's explicit plan."""
from __future__ import annotations

import copy
import itertools


def attach_check_plan(request: dict, inventory: list[dict]) -> dict:
    from .design_rules import apply_access_targets
    r = apply_access_targets(request)
    c, m = r["constraints"], r["constraints"]["manufacturing"]
    source = "deterministic check plan derived from reviewed constraints; reported scope excludes unimplemented physical analyses"
    frame = c["board_frame"]
    axes = [frame[f"{a}_axis"] for a in "xyz"]
    origin = frame["origin_mm"]
    matrix = [[*axis, -sum(a*b for a, b in zip(axis, origin))] for axis in axes] + [[0,0,0,1]]
    def bounds(item):
        corners = itertools.product(*zip(item["min_mm"], item["max_mm"]))
        points = [[sum(a*(x-o) for a,x,o in zip(axis,p,origin)) for axis in axes] for p in corners]
        return [min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]
    all_bounds = [bounds(x) for x in inventory]
    low = [min(lo[i] for lo,hi in all_bounds) for i in range(3)]
    high = [max(hi[i] for lo,hi in all_bounds) for i in range(3)]
    pcb_low, pcb_high = bounds(inventory[r["board"]["metadata"]["pcb_solid_index"]])
    lid_z = high[2] + m["clearance_top_mm"]
    margin = m["clearance_xy_mm"] + m["lid_fastener"]["post_diameter_mm"] + m["wall_thickness_mm"]
    lid_low = [low[0]-margin,low[1]-margin,lid_z]
    lid_high = [high[0]+margin,high[1]+margin,lid_z+m["lid_thickness_mm"]]
    if m.get("enclosure_fit") == "compact":
        from .cad.compact import body_and_footprint_budget
        budget = body_and_footprint_budget(
            [{"min_mm": lo, "max_mm": hi} for lo,hi in all_bounds],
            r["board"]["metadata"]["pcb_solid_index"], c["ports"], m, display=c.get("display"))
        lid_z = budget["lid_mating_plane_z_mm"]
        footprint = budget["footprint_upper_bound"]
        lid_low = [*footprint["min_mm"][:2],lid_z]
        lid_high = [*footprint["max_mm"][:2],lid_z+m["lid_thickness_mm"]]
    envelope = lambda lo,hi: {"kind":"box","min_mm":lo,"max_mm":hi,"provenance":source}
    options = {"planner":"justbuild-default-0.1", "geometry_frame":"confirmed_board_frame", "board_transform":matrix,
        "tolerance_mm":m["geometry_tolerance_mm"], "volume_tolerance_mm3":m["boolean_volume_tolerance_mm3"],
        "coverage":{scope:{"complete":True,"provenance":source} for scope in
                    ("board_geometry","intentional_contacts","clearances","mounting_holes","ports","keepouts","assembly_paths")},
        "intentional_contacts":[{"parts":["board","base"],"mode":"touch_only","reason":"PCB rests on confirmed supports","provenance":source,
                                 "regions":[envelope([h["center_mm"][0]-m["pcb_mount"]["post_diameter_mm"]/2-.01,
                                                      h["center_mm"][1]-m["pcb_mount"]["post_diameter_mm"]/2-.01,pcb_low[2]-.01],
                                                     [h["center_mm"][0]+m["pcb_mount"]["post_diameter_mm"]/2+.01,
                                                      h["center_mm"][1]+m["pcb_mount"]["post_diameter_mm"]/2+.01,pcb_low[2]+.01]) for h in c["mounting_holes"]]},
                                 {"parts":["base","lid"],"mode":"touch_only","reason":"lid seats on base rim and screw bosses","provenance":source,
                                  "regions":[envelope([lid_low[0]-.01,lid_low[1]-.01,lid_z-.01],[lid_high[0]+.01,lid_high[1]+.01,lid_z+.01])]}],
        "clearances":[{"parts":["board","base"],"minimum_mm":0,"provenance":"PCB support contact is intentional; lateral and floor clearances are checked separately"},
                      {"parts":["board","lid"],"minimum_mm":m["clearance_top_mm"],"provenance":source}],
        "mounting_checks":{}, "port_checks":{},
        "assembly_checks":[
            {"id":"pcb_vertical_installation","moving_part":"board","envelope":envelope(low,high),
             "translation_mm":[0,0,max(30,lid_z-low[2]+10)],"against":["base"],"absent_parts":["lid"],
             "stage_provenance":"PCB is installed before the lid is fitted; extraction is the reverse sequence",
             "minimum_clearance_mm":0,"provenance":source},
            {"id":"lid_vertical_installation","moving_part":"lid","envelope":envelope(lid_low,lid_high),
             "translation_mm":[0,0,30],"against":["base","board"],"minimum_clearance_mm":0,"provenance":source}],
    }
    if m.get("enclosure_fit") == "compact":
        # The actual pierced lid, not a solid rectangle filling every opening.
        options["assembly_checks"][1]["envelope"] = {
            "kind": "moving_part_brep", "provenance": source + "; exact full exported lid BRep continuous axial translation"}
    for h in c["mounting_holes"]:
        h["provenance"] = h["source"]
        start = pcb_low[2] - m["pcb_mount"]["pilot_depth_mm"] - h["center_mm"][2]
        end = pcb_low[2] - h["center_mm"][2]
        options["mounting_checks"][h["id"]] = [{"part_id":"base","span_mm":[start,end],
            "minimum_diameter_mm":m["pcb_mount"]["pilot_diameter_mm"],"maximum_diameter_mm":m["pcb_mount"]["pilot_diameter_mm"]+.002,
            "position_tolerance_mm":.001,"angle_tolerance_deg":.01,"provenance":source}]
    for p in c["ports"]:
        p["access_mode"] = p["access"]
        p["provenance"] = p["source"]
        if p["access"] not in ("external","maintenance"):
            continue
        service = p["service_envelope"]
        spec = envelope(service["min_mm"], service["max_mm"])
        spec["provenance"] = service["source"]
        options["port_checks"][p["id"]] = {"opening_envelope":spec,"against":["base","lid"],"minimum_clearance_mm":0,
            "insertion":{"envelope":spec,"translation_mm":[-30*x for x in p["insertion_direction"]],
                         "against":["base","lid","board"],"minimum_clearance_mm":0,"provenance":source}}
    if c.get("display", {}).get("support_structure") == "removable_pcb_carrier":
        for entry in options["port_checks"].values():
            entry["against"] = ["base", "lid", "pcb_carrier", "screen"]
            entry["insertion"]["against"] = ["base", "lid", "pcb_carrier", "screen", "board"]
    for k in c["keepouts"]:
        k["provenance"] = k["source"]
        k["geometry"] = envelope(k["min_mm"], k["max_mm"])
        k["against"] = (["base","lid","pcb_carrier","screen"]
                        if c.get("display", {}).get("support_structure") == "removable_pcb_carrier" else ["base","lid"])
        k["minimum_clearance_mm"] = 0
    # Preserve caller-supplied explicit plans; never overwrite a stricter declared check.
    if "verification" not in r or r["verification"].get("planner") == "justbuild-default-0.1":
        r["verification"] = options
    return r


def supplemental_clearances(request: dict, cad: dict, cad_path) -> list[dict]:
    """BRep distance to the floor and side-wall ring, excluding deliberate support contacts."""
    import cadquery as cq
    from .contracts import resolve_artifact
    artifacts = {a["kind"]: a for a in cad["artifacts"]}
    board = cq.importers.importStep(str(resolve_artifact(cad_path,artifacts["normalized_input_step"]))).val()
    base = cq.importers.importStep(str(resolve_artifact(cad_path,artifacts["base_step"]))).val()
    cavity = cad["metadata"]["cavity_bounds"]
    outer = cad["metadata"]["outer_bounds"]
    lo,hi = cavity["min_mm"],cavity["max_mm"]
    def box(low,high):
        return cq.Workplane("XY").box(*[b-a for a,b in zip(low,high)],centered=(False,False,False)).translate(tuple(low)).val()
    ring = base.cut(box([lo[0],lo[1],outer["min_mm"][2]-1],[hi[0],hi[1],outer["max_mm"][2]+1]))
    floor = base.intersect(box(outer["min_mm"],[*outer["max_mm"][:2],lo[2]]))
    m = request["constraints"]["manufacturing"]
    results=[]
    for name,shape,required in (("side_wall_clearance",ring,m["clearance_xy_mm"]),("floor_clearance",floor,m["clearance_bottom_mm"])):
        if not shape.Solids():
            results.append({"id":name,"category":"clearances","status":"not_checked","required":True,
                            "method":"OCC_BRep_minimum_distance_to_isolated_shell_region","measurements":{"reason":"isolated region has no valid solids"}})
            continue
        distance=board.distance(shape)
        results.append({"id":name,"category":"clearances","status":"pass" if distance+m["geometry_tolerance_mm"]>=required else "fail",
                        "required":True,"method":"OCC_BRep_minimum_distance_to_isolated_shell_region",
                        "tolerance_mm":m["geometry_tolerance_mm"],"measurements":{"minimum_distance_mm":distance,"required_minimum_mm":required,
                        "region":"full-height side-wall ring excluding interior support posts" if name.startswith("side") else "floor below the cavity plane"}})
    return results


def functional_access_checks(request: dict) -> list[dict]:
    """Report functional inventory links separately from unmodeled operation.

    This checks the declared named inventory, not the semantics or physical
    travel of a connector. The independent verifier checks its window geometry.
    """
    constraints = request.get("constraints", {})
    features = constraints.get("functional_features", [])
    if not features:
        return []
    ports = {port["id"] for port in constraints.get("ports", [])}
    missing = [feature.get("id") for feature in features
               if not feature.get("source") or not feature.get("solid_indices")
               or feature.get("reviewed") is not True or not feature.get("port_ids")
               or any(identity not in ports for identity in feature.get("port_ids", []))]
    result = [{"id": "functional_access_inventory", "category": "functional_access",
               "status": "fail" if missing else "pass", "required": True,
               "method": "declared_named_component_inventory_to_access_window_links",
               "measurements": {"feature_count": len(features), "missing_or_unreviewed": missing,
                                "source_sha256": constraints.get("functional_scope", {}).get("source_sha256"),
                                "scope": "provided STEP named components; independent BRep window checks remain separate"}}]
    analyses = {
        "button_switch_travel": "Actual button and switch travel, stops, force and tool operation are not provided by the static STEP.",
        "connector_mating": "Matching plugs and full engagement geometry are absent; static service boxes are design access windows.",
        "card_latch_motion": "Card ejection, SIM/FFC latch motion and manipulation are not represented by static component bounds.",
        "accessory_cable_fit": "Actual camera/display/antenna/MIC/SPK accessories and cable routing/strain relief are not modeled.",
        "acoustic_performance": "The buzzer has a geometric sound opening; acoustic performance is not measured.",
    }
    for name, reason in analyses.items():
        result.append({"id": name, "category": name, "status": "not_checked", "required": False,
                       "method": "not_performed_missing_physical_inputs", "measurements": {"reason": reason}})
    return result

