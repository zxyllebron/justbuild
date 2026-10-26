"""Auditable continuation when optional STEP reencoding fails fidelity checks.

Only identity-frame, native-mm source bytes may be reused. Prior failures stay
immutable; a fresh result references the checks already performed and rechecks
the required generated case solids. Independent verification remains mandatory.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile

from . import sha256
from .kernel import bbox, read_step
from .request import validate, need


def native_mm_units(path: Path) -> list[str]:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    reader = STEPControl_Reader()
    need(reader.ReadFile(str(path)) == IFSelect_RetDone, "Cannot inspect source STEP units")
    lengths, angles, solids = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.FileUnits(lengths, angles, solids)
    units = [lengths.Value(i).ToCString() for i in range(1, lengths.Length()+1)]
    text = path.read_text(encoding="utf-8", errors="strict")
    declarations = re.findall(r"LENGTH_UNIT\s*\([^)]*\).*?SI_UNIT\s*\(([^)]*)\)", text, re.S)
    need(bool(units) and all(unit.lower() in ("millimetre", "millimeter") for unit in units)
         and bool(declarations) and all(re.sub(r"\s+", "", v) == ".MILLI.,.METRE." for v in declarations)
         and "CONVERSION_BASED_UNIT" not in text,
         "Source preservation requires exclusively native millimeter length units")
    return units


def resume_identity_source_preservation(request_path: Path, previous_result_path: Path,
                                      output_dir: Path) -> Path:
    import cadquery as cq
    request_path, previous_result_path = request_path.resolve(), previous_result_path.resolve()
    old = json.loads(previous_result_path.read_text())
    request = json.loads(request_path.read_text())
    config = validate(request, request_path.parent)
    identity = {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}
    need(request["constraints"]["board_frame"] == identity,
         "Byte-preserving recovery requires an exact identity board frame")
    need(old.get("status") == "failed" and old.get("job_id") == request["job_id"], "Wrong recovery input")
    provenance = old["metadata"]["input_provenance"]
    need(sha256(request_path) == provenance["request_sha256"], "Frozen request has changed")
    source = previous_result_path.parent / provenance["consumed_step_snapshot"]
    need(sha256(source) == provenance["step_sha256"] == sha256(config["step_path"]), "Source bytes have changed")
    failures = [c for c in old["checks"] if c["status"] == "fail"]
    need(len(failures) == 1 and failures[0]["name"] == "normalized_input_step_roundtrip",
         "Only an optional source-reencoding failure can use this continuation")
    required = {"base_valid_single_solid", "lid_valid_single_solid", "base_step_roundtrip", "lid_step_roundtrip",
                "base_input_nonpenetration", "lid_input_nonpenetration", "base_lid_nonpenetration"}
    removable = request["constraints"].get("display", {}).get("support_structure") == "removable_pcb_carrier"
    if removable:
        required |= {"pcb_carrier_valid_single_solid", "pcb_carrier_step_roundtrip", "pcb_carrier_input_nonpenetration",
                     "pcb_carrier_base_nonpenetration", "pcb_carrier_lid_nonpenetration", "display_pcb_carrier_nonpenetration"}
    passed = {c["name"] for c in old["checks"] if c["status"] == "pass"}
    need(required <= passed and not old.get("artifacts"), "Prior case checks are incomplete")
    units = native_mm_units(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="cad-preserved-", dir=output_dir))
    prior_hash = sha256(previous_result_path)
    result = deepcopy(old)
    result["status"] = "ok"
    result["issues"] = [{"code": "OPTIONAL_SOURCE_REENCODING_REJECTED", "severity": "warning",
                          "message": "Original native-mm source is preserved byte-for-byte at identity placement; drifting reencoded source and optional assembly STEP are not published.",
                          "evidence": {"prior_result_sha256": prior_hash, "rejected_check": failures[0]}}]
    result["checks"] = [{**deepcopy(c), "reused_check_provenance": {
        "prior_result_sha256": prior_hash, "prior_check_index": i, "same_request_sha256": provenance["request_sha256"],
        "same_source_sha256": provenance["step_sha256"]}} for i, c in enumerate(old["checks"]) if c["status"] == "pass"]
    metadata = result["metadata"]
    metadata["recovery"] = {"policy": "identity_source_preservation", "prior_result_sha256": prior_hash,
                            "prior_result_status": old["status"], "independent_verification_required": True,
                            "assembly_step": "omitted: source reencoding fidelity was not established"}
    metadata["input_provenance"]["identity_source_preservation"] = {"frame": identity, "length_units": units,
        "source_sha256": provenance["step_sha256"], "method": "exact source byte copy; no STEP geometry reencoding"}
    # Reimport the generated case, binding this new result to the actual files.
    parts = {}
    for name in (("base", "lid", "pcb_carrier") if removable else ("base", "lid")):
        old_file = previous_result_path.parent / f"{name}.step"
        before = next(c["measurements"] for c in old["checks"] if c["name"] == f"{name}_valid_single_solid")
        shape = read_step(cq, old_file)
        delta = abs(shape.Volume()-before["volume_mm3"])
        vtol = request["constraints"]["manufacturing"]["boolean_volume_tolerance_mm3"]
        need(shape.isValid() and len(shape.Solids()) == 1 and delta <= vtol,
             "Generated case checkpoint no longer matches its recorded geometry", part=name, volume_delta_mm3=delta)
        need(all(abs(bbox(shape)[side][i]-before["bounds"][side][i]) <= 1e-7
                 for side in ("min_mm", "max_mm") for i in range(3)), "Case checkpoint bounds changed", part=name)
        shutil.copyfile(old_file, out / old_file.name)
        parts[name] = shape
        result["checks"].append({"name": f"recovered_{name}_step_checkpoint", "status": "pass",
                                "method": "Fresh BRep import, validity/count and agreement with recorded generated solid",
                                "tolerance": {"volume_mm3": vtol}, "measurements": {"volume_delta_mm3": delta,
                                "sha256": sha256(old_file)}})
    if metadata.get("display"):
        screen_path = previous_result_path.parent / "screen_envelope.step"
        screen = read_step(cq, screen_path)
        bounds = metadata["display"]["envelope_bounds"]
        need(screen.isValid() and len(screen.Solids()) == 1
             and all(abs(bbox(screen)[side][i]-bounds[side][i]) <= 1e-7
                     for side in ("min_mm", "max_mm") for i in range(3)),
             "Display envelope checkpoint no longer matches its recorded geometry")
        shutil.copyfile(screen_path, out / "screen_envelope.step")
    for name in (("base.stl", "lid.stl", "pcb_carrier.stl", "review.glb") if removable else ("base.stl", "lid.stl", "review.glb")):
        path = previous_result_path.parent / name
        need(path.is_file() and path.stat().st_mtime_ns <= previous_result_path.stat().st_mtime_ns,
             "Generated mesh checkpoint is absent or modified after the failure", checkpoint=name)
        shutil.copyfile(path, out / name)
    shutil.copyfile(source, out / "source.step")
    shutil.copyfile(source, out / "normalized_input.step")
    shutil.copyfile(request_path, out / "request_snapshot.json")
    need(sha256(out / "normalized_input.step") == provenance["step_sha256"], "Source copy changed")
    result["checks"].append({"name": "normalized_input_byte_preservation", "status": "pass",
        "method": "SHA-256 byte equality with validated original source, exact identity frame and native-mm STEP units",
        "tolerance": {"byte_difference": 0}, "measurements": {"source_sha256": provenance["step_sha256"],
        "length_units": units, "source_solid_count": len(metadata["input_solids"])}})
    _complete_metadata(metadata, request, parts)
    manifest = {"schema_version": "0.1", "job_id": request["job_id"], "units": "mm",
                "source_preserved_byte_for_byte": True, "frame": identity,
                "parts": [{"kind": kind, "path": filename, "sha256": sha256(out / filename),
                           "transform": "identity; same board coordinate system"}
                          for kind, filename in (("normalized_input_step", "normalized_input.step"),
                                                 ("base_step", "base.step"), ("lid_step", "lid.step"))]}
    if removable:
        manifest["parts"].append({"kind": "pcb_carrier_step", "path": "pcb_carrier.step",
                                  "sha256": sha256(out / "pcb_carrier.step"), "transform": "identity; same board frame"})
    if metadata.get("display"):
        manifest["parts"].append({"kind": "screen_envelope_step", "path": "screen_envelope.step",
                                  "sha256": sha256(out / "screen_envelope.step"),
                                  "transform": "identity; declared user-dimension envelope in board frame",
                                  "manufacturer_component_model": False})
    (out / "assembly_manifest.json").write_text(json.dumps(manifest, indent=2))
    names = [("base_step", "base.step", "mm"), ("lid_step", "lid.step", "mm"),
             ("base_stl", "base.stl", "mm"), ("lid_stl", "lid.stl", "mm"),
             ("review_glb", "review.glb", "m"), ("normalized_input_step", "normalized_input.step", "mm"),
             ("source_step", "source.step", None), ("request_snapshot", "request_snapshot.json", None),
             ("assembly_manifest", "assembly_manifest.json", None)]
    if metadata.get("display"):
        names.append(("screen_envelope_step", "screen_envelope.step", "mm"))
    if removable:
        names.extend([("pcb_carrier_step", "pcb_carrier.step", "mm"), ("pcb_carrier_stl", "pcb_carrier.stl", "mm")])
    result["artifacts"] = [{"kind": kind, "path": filename, "sha256": sha256(out / filename),
                            **({"units": unit} if unit else {})} for kind, filename, unit in names]
    for name in ("assembly_path", "thermal", "fastener_strength", "manufacturing"):
        result["checks"].append({"name": name, "status": "not_checked", "method": "not performed", "tolerance": None, "measurements": {}})
    path = out / "cad_result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    (out / "progress.json").write_text(json.dumps({"schema_version": "0.1", "phase": "finished",
        "status": "ok", "policy": "identity_source_preservation", "result_filename": path.name,
        "updated_at": datetime.now(timezone.utc).isoformat()}))
    return path


def _complete_metadata(metadata, request, parts):
    """Record the same deterministic construction dimensions as the checked run."""
    c = request["constraints"]
    m = c["manufacturing"]
    if m.get("enclosure_fit", "legacy") == "compact":
        # Compact geometry has local ear/recess topology. Reconstructing it
        # with the legacy full-ring reservation would falsify the checkpoint.
        need(metadata.get("enclosure_fit", {}).get("mode") == "compact"
             and "body_outer_bounds" in metadata and "lid_screw_centers_mm" in metadata,
             "Compact recovery requires original checked construction metadata")
        for name, part in parts.items():
            before = next(row["bounds"] for row in metadata["parts"] if row["id"] == name)
            need(all(abs(bbox(part)[side][i]-before[side][i]) <= 1e-7
                     for side in ("min_mm", "max_mm") for i in range(3)),
                 "Compact case metadata no longer matches its BRep", part=name)
        return
    placement = metadata["board_placement"]
    pl = placement["pcb_bounds"]["min_mm"]
    all_b = placement["assembly_bounds"]
    r = m["lid_fastener"]["post_diameter_mm"]/2
    wall, gap = m["wall_thickness_mm"], m["clearance_xy_mm"]
    floor = pl[2]-m["standoff_height_mm"]
    lid_z = all_b["max_mm"][2]+m["clearance_top_mm"]
    inner_lo = [all_b["min_mm"][i]-gap-2*r for i in (0,1)]
    inner_hi = [all_b["max_mm"][i]+gap+2*r for i in (0,1)]
    outer_lo = [inner_lo[0]-wall,inner_lo[1]-wall,floor-m["base_thickness_mm"]]
    outer_hi = [inner_hi[0]+wall,inner_hi[1]+wall,lid_z+m["lid_thickness_mm"]]
    overlap = min(wall/2,r/4)
    screws = [[x,y,lid_z] for x in (inner_lo[0]+r-overlap,inner_hi[0]-r+overlap)
              for y in (inner_lo[1]+r-overlap,inner_hi[1]-r+overlap)]
    contacts = [{"feature_id":h["id"],"parts":["base",f"input_solid_{placement['pcb_solid_index']}"],
                 "kind":"pcb_standoff_annular_face","center_mm":[*h["center_mm"][:2],pl[2]],
                 "normal":[0,0,1],"outer_diameter_mm":m["pcb_mount"]["post_diameter_mm"],
                 "inner_diameter_mm":h["diameter_mm"]} for h in c["mounting_holes"]]
    contacts.append({"parts":["base","lid"],"kind":"planar_rim_and_screw_post_contact","plane_z_mm":lid_z,"normal":[0,0,1]})
    metadata.update(generated_configuration=m, outer_bounds={"min_mm":outer_lo,"max_mm":outer_hi},
                    cavity_bounds={"min_mm":[*inner_lo,floor],"max_mm":[*inner_hi,lid_z]},
                    parts=[{"id":n,"bounds":bbox(s),"volume_mm3":s.Volume()} for n,s in parts.items()],
                    lid_screw_centers_mm=screws,corner_post_wall_overlap_mm=overlap,intentional_contacts=contacts,
                    ports=c["ports"],keepouts=c["keepouts"],mounting_holes=c["mounting_holes"],
                    stl_placements={n:{"translation_mm":[-outer_lo[0],-outer_lo[1],-z],"rotation":"identity","bed_plane_z_mm":0}
                                    for n,z in (("base",outer_lo[2]),("lid",lid_z))},
                    review_glb_frame={"units":"m","mapping_from_board_mm":"[x, z, -y] * 0.001"},
                    hardware={"modeled":False,"pcb_mount":m["pcb_mount"],"lid_fastener":m["lid_fastener"]})
