"""Versioned prototype fit targets and independent shell-surface measurements.

These rules do not establish plug engagement, button travel or printed tolerances.
Only new requests that explicitly contain fit_policy opt into these checks.
"""
from __future__ import annotations

import copy
import math

POLICY_ID = "compact-access-v1"


def default_fit_policy() -> dict:
    return {"id": POLICY_ID, "units": "mm", "maximum_ordinary_side_gap_mm": 1.0,
            "connector_maximum_recess_mm": 0.5, "button_minimum_protrusion_mm": 0.0,
            "source": "2026-10-26 user physical-fit feedback; prototype targets requiring print calibration"}


def apply_access_targets(request: dict) -> dict:
    r = copy.deepcopy(request)
    c = r.get("constraints", {})
    policy = c.get("manufacturing", {}).get("fit_policy", {})
    if policy.get("id") != POLICY_ID:
        return r
    button_ports = set()
    acoustic_ports = set()
    for feature in c.get("functional_features", []):
        kind = str(feature.get("kind", "")).lower()
        if "button" in kind or "switch" in kind:
            button_ports.update(feature.get("port_ids", []))
        if "buzzer" in kind or kind in ("sounder", "acoustic vent"):
            acoustic_ports.update(feature.get("port_ids", []))
    for port in c.get("ports", []):
        if port.get("access") not in ("external", "maintenance"):
            continue
        button = port.get("id") in button_ports or port.get("component_kind") in ("button", "switch")
        if not button and port.get("id") in acoustic_ports:
            # A source-identified sound aperture has no plug mating surface.
            # Keep the geometric opening checks; no acoustic performance claim.
            port.setdefault("fit_requirement", "acoustic_vent")
            continue
        if button or port.get("access") == "external":
            if "component_solid_indices" not in port and type(port.get("solid_index")) is int:
                port["component_solid_indices"] = [port["solid_index"]]
            target = 0.0 if button else policy["connector_maximum_recess_mm"]
            settings = port.setdefault("surface_access", {})
            settings.setdefault("max_recess_mm", target)
            settings.setdefault("recess_margin_mm", 1.5)
            port.setdefault("fit_requirement", "exposed_button" if button else "near_flush_connector")
    return r


def fit_policy_checks(request: dict, cad: dict, cad_path) -> list[dict]:
    """Measure real BRep wall gaps and material adjacent to each required opening.

    A local lip is measured using a narrow collar around the unchanged opening.
    Empty collars, missing named solids and unsupported geometry fail closed.
    Static component extrema are explicitly distinct from an actuator's travel.
    """
    c = request.get("constraints", {})
    m = c.get("manufacturing", {})
    policy = m.get("fit_policy")
    if not policy:
        return []
    results = []
    def add(identity, status, measurements, category="compact_fit"):
        results.append({"id": identity, "category": category, "status": status, "required": True,
                        "method": "independent_exported_BRep_shell_surface_measurement",
                        "tolerance_mm": m.get("geometry_tolerance_mm", 0.001), "measurements": measurements})
    names = ("maximum_ordinary_side_gap_mm", "connector_maximum_recess_mm", "button_minimum_protrusion_mm")
    if (not isinstance(policy, dict) or policy.get("id") != POLICY_ID or
            any(type(policy.get(n)) not in (int, float) or not math.isfinite(policy[n]) or policy[n] < 0 for n in names)):
        add("compact_fit_policy", "fail", {"reason": "Unsupported or invalid explicit fit policy"})
        return results
    if m.get("enclosure_fit") != "compact":
        add("compact_fit_layout", "fail", {"reason": "Fit policy requires explicit compact layout"})
    try:
        import cadquery as cq
        from .contracts import resolve_artifact
        artifacts = {a["kind"]: a for a in cad["artifacts"]}
        source = cq.importers.importStep(str(resolve_artifact(cad_path, artifacts["normalized_input_step"]))).val()
        source_solids = source.Solids()
        pcb = source_solids[request["board"]["metadata"]["pcb_solid_index"]]
        base = cq.importers.importStep(str(resolve_artifact(cad_path, artifacts["base_step"]))).val()
        lid = cq.importers.importStep(str(resolve_artifact(cad_path, artifacts["lid_step"]))).val()
        shell = cq.Compound.makeCompound([base, lid])
        def bounds(shape):
            b = shape.BoundingBox()
            return [b.xmin,b.ymin,b.zmin], [b.xmax,b.ymax,b.zmax]
        def box(lo,hi):
            if any(b <= a for a,b in zip(lo,hi)):
                raise ValueError("Degenerate sampling region")
            return cq.Solid.makeBox(*[b-a for a,b in zip(lo,hi)], cq.Vector(*lo))
        pl,ph = bounds(pcb);sl,sh = bounds(shell)
        tol = m["geometry_tolerance_mm"]
        for axis in (0,1):
            tangent = 1-axis
            for side in ("min", "max"):
                lo,hi = sl.copy(),sh.copy()
                lo[tangent] = pl[tangent]+(ph[tangent]-pl[tangent])*.1
                hi[tangent] = ph[tangent]-(ph[tangent]-pl[tangent])*.1
                lo[2] = pl[2]+(ph[2]-pl[2])*.25
                hi[2] = pl[2]+(ph[2]-pl[2])*.75
                if side == "min":hi[axis] = pl[axis]
                else:lo[axis] = ph[axis]
                material = base.intersect(box(lo,hi))
                identity = "compact_side_gap_"+"xy"[axis]+"_"+side
                if not material.Solids():
                    add(identity,"not_checked",{"reason":"No adjacent side wall in measured PCB-height strip"})
                    continue
                a,b = bounds(material)
                gap = pl[axis]-b[axis] if side == "min" else a[axis]-ph[axis]
                maximum = policy["maximum_ordinary_side_gap_mm"]
                exception = None
                display = c.get("display", {})
                size = display.get("size_mm", []) if isinstance(display,dict) else []
                display_gap = display.get("clearance_xy_mm") if isinstance(display,dict) else None
                if (display.get("orientation") == "pcb_back_parallel" and isinstance(size,list) and len(size)==3
                        and all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in size)
                        and type(display_gap) in (int,float) and math.isfinite(display_gap) and display_gap>=0):
                    needed = (size[axis]-(ph[axis]-pl[axis]))/2+display_gap
                    if needed>maximum:
                        maximum=needed
                        exception="Explicit user-sized centered rear display; separate independent display envelope and PCB mounting checks required"
                add(identity,"pass" if gap <= maximum+tol and gap >= -tol else "fail",
                    {"side_gap_mm":gap,"maximum_mm":maximum,
                     "ordinary_maximum_mm":policy["maximum_ordinary_side_gap_mm"],"justified_accessory_space":exception,
                     "scope":"PCB-height straight side-wall strip; local corner fasteners excluded"})
        effective = apply_access_targets(request)
        for port in effective["constraints"].get("ports",[]):
            if port.get("fit_requirement") not in ("exposed_button", "near_flush_connector"):
                continue
            identity = "surface_access_"+port["id"]
            indices = port.get("component_solid_indices")
            if not indices and type(port.get("solid_index")) is int:indices=[port["solid_index"]]
            if (not isinstance(indices,list) or not indices or any(type(i) is not int or not 0<=i<len(source_solids) for i in indices)):
                add(identity,"not_checked",{"reason":"Named component STEP solids required for an independent static outer-face datum"},"surface_access")
                continue
            face = port.get("face","")
            if face not in ("x_min","x_max","y_min","y_max","z_min","z_max") or port.get("shape") not in ("rectangle","circle"):
                add(identity,"not_checked",{"reason":"Unsupported operating surface"},"surface_access")
                continue
            axis="xyz".index(face[0]);sign=-1 if face.endswith("min") else 1
            comp = cq.Compound.makeCompound([source_solids[i] for i in indices])
            cl,ch=bounds(comp);datum=cl[axis] if sign<0 else ch[axis]
            tangents=[i for i in range(3) if i!=axis]
            center=port["center_mm"]
            if port["shape"]=="rectangle":dims=[port["width_mm"],port["height_mm"]]
            else:dims=[port["diameter_mm"]]*2
            # A declared large open operation area is distinct from a small deep
            # window. Prove its complete empty approach volume in exported BRep.
            open_result = None
            positive_target = port.get("surface_access", {}).get("min_protrusion_mm", 0.0)
            if type(positive_target) not in (int,float) or not math.isfinite(positive_target) or positive_target < 0:
                add(identity,"fail",{"reason":"Invalid explicit local protrusion target"},"surface_access")
                continue
            # A positive protrusion target requires actual nearby lip geometry;
            # a distant open operation area cannot prove that relationship.
            regions = c.get("open_operation_regions", []) if port["fit_requirement"] == "exposed_button" and positive_target == 0 else []
            for region in regions:
                if region.get("shape") != "box" or not region.get("source"):
                    continue
                rl,rh=region.get("min_mm",[]),region.get("max_mm",[])
                if len(rl)!=3 or len(rh)!=3:
                    continue
                projection=all(rl[t]<=center[t]-dim/2-0.5+tol and rh[t]>=center[t]+dim/2+0.5-tol
                               and rh[t]-rl[t]>=16.0-tol for t,dim in zip(tangents,dims))
                reaches=(rl[axis]<=datum+tol and rh[axis]>=sh[axis]-tol) if sign>0 else (rh[axis]>=datum-tol and rl[axis]<=sl[axis]+tol)
                if not projection or not reaches:
                    continue
                occupied=shell.intersect(box(rl,rh))
                volume=sum(abs(part.Volume()) for part in occupied.Solids())
                open_result={"region_id":region.get("id"),"shell_material_volume_mm3":volume,
                             "static_component_outer_face_mm":datum,
                             "access_mode":"open_operation_access", "opening_projection_fully_contained":True,
                             "scope":"Large declared open approach volume; does not claim protrusion beyond remote cover, actuation stroke or finger-force validation"}
                if volume <= m.get("boolean_volume_tolerance_mm3",1e-7):
                    add(identity,"pass",open_result,"surface_access")
                    break
            else:
                open_result=None
            if open_result is not None:
                continue
            band=0.5
            lo,hi=sl.copy(),sh.copy();il,ih=sl.copy(),sh.copy()
            for t,dim in zip(tangents,dims):
                lo[t]=center[t]-dim/2-band;hi[t]=center[t]+dim/2+band
                il[t]=center[t]-dim/2-tol;ih[t]=center[t]+dim/2+tol
            panel_thickness = port.get("surface_access", {}).get("panel_thickness_mm", 0.0)
            if positive_target and (type(panel_thickness) not in (int,float) or not math.isfinite(panel_thickness) or panel_thickness < 0):
                add(identity,"fail",{"reason":"Invalid local panel thickness for protrusion measurement"},"surface_access")
                continue
            # A projecting tip lies OUTSIDE its nearby panel. Include a bounded
            # inward band to find the real panel, without admitting the opposite
            # case wall as evidence when the local panel is absent.
            back = tol + (positive_target + panel_thickness + band if positive_target else 0.0)
            if sign<0:hi[axis]=ih[axis]=max(datum+back,sl[axis]+tol)
            else:lo[axis]=il[axis]=min(datum-back,sh[axis]-tol)
            bay = port.get("surface_access", {})
            open_lower_plane = None
            if bay.get("mode") == "side_u_bay":
                open_lower_plane = bay.get("open_bottom_z_mm")
                if (axis == 2 or type(open_lower_plane) not in (int,float)
                        or not math.isfinite(open_lower_plane)
                        or open_lower_plane >= hi[2]-tol):
                    add(identity,"fail",{"reason":"Invalid explicit open-sided bay geometry"},"surface_access")
                    continue
                # An open lower side has no local lower lip. First prove the
                # entire unchanged opening is clear through BOTH shell parts;
                # only then measure the remaining true local upper/side lips.
                approach_lo,approach_hi=sl.copy(),sh.copy()
                # The half-mm lip search band is for finding adjacent material;
                # it is not an extra cut required behind the complete panel.
                approach_back = tol + (positive_target + panel_thickness if positive_target else 0.0)
                for t,dim in zip(tangents,dims):
                    approach_lo[t]=center[t]-dim/2
                    approach_hi[t]=center[t]+dim/2
                if sign < 0:
                    approach_lo[axis] -= tol
                    approach_hi[axis] = max(datum+approach_back,sl[axis]+tol)
                else:
                    approach_lo[axis] = min(datum-approach_back,sh[axis]-tol)
                    approach_hi[axis] += tol
                occupied=shell.intersect(box(approach_lo,approach_hi))
                occupied_volume=sum(abs(part.Volume()) for part in occupied.Solids())
                if occupied_volume > m.get("boolean_volume_tolerance_mm3",1e-7):
                    add(identity,"fail",{"reason":"Material obstructs the unchanged side-bay opening",
                        "shell_material_volume_mm3":occupied_volume},"surface_access")
                    continue
                lo[2]=max(lo[2],open_lower_plane)
                il[2]=max(il[2],open_lower_plane)
            collar=box(lo,hi).cut(box(il,ih))
            material=shell.intersect(collar)
            if not material.Solids():
                add(identity,"not_checked",{"reason":"No local shell lip in the opening collar; an explicit open-sided treatment needs separate geometry evidence"},"surface_access")
                continue
            a,b=bounds(material);surface=a[axis] if sign<0 else b[axis]
            protrusion=sign*(datum-surface)
            maximum=0.0 if port["fit_requirement"]=="exposed_button" else policy["connector_maximum_recess_mm"]
            minimum=max(policy["button_minimum_protrusion_mm"],positive_target) if port["fit_requirement"]=="exposed_button" else -maximum
            add(identity,"pass" if protrusion+tol>=minimum else "fail",
                {"static_component_outer_face_mm":datum,"local_shell_outer_surface_mm":surface,
                 "signed_protrusion_mm":protrusion,"minimum_protrusion_mm":minimum,
                 "component_solid_indices":indices,"reference":"actual named STEP component outer extent and exported shell BRep collar",
                 "open_lower_plane_mm":open_lower_plane,
                 "full_unchanged_opening_clear":True if open_lower_plane is not None else None,
                 "scope":"static exposure only; explicit side U bays measure upper/side lips above their open lower edge; button stroke, plug engagement and printed fit remain unverified"},"surface_access")
    except Exception as exc:
        add("compact_fit_geometry_evidence","not_checked",{"reason":"Independent fit measurement unavailable", "exception_type":type(exc).__name__})
    return results
