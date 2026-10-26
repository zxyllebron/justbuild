"""Verification-gated creation of an immutable review-ready slicing job."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from .common import (OutputOverlapError, PrintError, artifact, checked_file, digest, envelope, positive_number, protect_output,
                     read_json, record_error, require_envelope, sha256, text_field, write_json)
from .slicer import Slicer, inspect_sliced_3mf


def _snapshot(source: Path, target: Path, expected: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha256(target) != digest(expected):
        raise PrintError("INPUT_CHANGED", "Input changed while creating the slicing snapshot.")
    return target


def _validate_profiles(profiles: dict[str, Path], settings: dict) -> None:
    required = {
        "machine": ("printer_model", "nozzle_diameter", "printable_area", "printable_height", "machine_start_gcode", "machine_end_gcode"),
        "process": ("layer_height", "wall_loops", "sparse_infill_density", "compatible_printers"),
        "filament": ("filament_type", "filament_diameter", "nozzle_temperature", "compatible_printers"),
    }
    configs = {}
    for role, path in profiles.items():
        config = read_json(path)
        if config.get("type") != role or config.get("inherits") or config.get("include"):
            raise PrintError("PROFILE_INCOMPLETE", "CLI profiles must be flattened full profiles.", "needs_input")
        if any(key not in config for key in required[role]):
            raise PrintError("PROFILE_INCOMPLETE", "A CLI profile lacks required settings.", "needs_input")
        if config.get("name") != settings["profiles"][role]["id"]:
            raise PrintError("PROFILE_IDENTITY", "Pinned profile identity differs from its contents.")
        configs[role] = config
    machine = configs["machine"]
    if machine.get("printer_model") != "Bambu Lab A1":
        raise PrintError("PRINTER_MODEL", "This adapter supports Bambu Lab A1 only.", "unsupported")
    nozzle = machine.get("nozzle_diameter")
    if not isinstance(nozzle, list) or len(nozzle) != 1 or positive_number(nozzle[0], "nozzle") != settings["nozzle_diameter_mm"]:
        raise PrintError("NOZZLE_MISMATCH", "Machine profile nozzle differs from the pinned nozzle.")
    material = configs["filament"].get("filament_type")
    if material != [settings["material"]]:
        raise PrintError("MATERIAL_MISMATCH", "Filament profile differs from the selected material.")
    for role in ("process", "filament"):
        compatible = configs[role].get("compatible_printers")
        if not isinstance(compatible, list) or machine["name"] not in compatible:
            raise PrintError("PROFILE_COMPATIBILITY", "Explicit A1/nozzle compatibility is required in both profiles.", "needs_input")


def _check_plate_models(path: Path, models: list[Path]) -> None:
    """A real three-part slice must retain all three objects on its plate."""
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as archive:
            ref = archive.getinfo("Metadata/slice_info.config")
            if ref.file_size > 1024 * 1024:
                raise ValueError()
            data = archive.read(ref)
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError()
        plates = ET.fromstring(data).findall("plate")
        if len(plates) != 1:
            raise ValueError()
        objects = plates[0].findall("object")
        if (len(objects) != len(models)
                or {obj.get("name") for obj in objects} != {model.name for model in models}
                or any(obj.get("skipped") not in (None, "false", "0") for obj in objects)):
            raise ValueError()
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, ET.ParseError):
        raise PrintError("SLICED_MODELS_MISSING", "The sliced first plate must retain base, lid and removable PCB carrier objects.") from None


def prepare_print(request_path: Path, cad_result_path: Path, verification_path: Path,
                  output_dir: Path, *, slicer: Slicer | None = None) -> Path:
    """Create print_job.json; all other output is under a unique operation folder.

    No profile or material defaults are assumed. An omitted slicer returns
    unsupported. The HTTP orchestrator must inject its trusted deployed adapter.
    """
    output_dir = Path(output_dir).resolve()
    protect_output(output_dir, "print_job.json", [request_path, cad_result_path, verification_path])
    result = envelope()
    result.update({"operation": "prepare_print", "state": "blocked", "simulation": False})
    try:
        request_path, cad_result_path, verification_path = map(Path, (request_path, cad_result_path, verification_path))
        source_hashes = {"request_sha256": sha256(request_path), "cad_result_sha256": sha256(cad_result_path),
                         "verification_sha256": sha256(verification_path)}
        request, cad, verification = map(read_json, (request_path, cad_result_path, verification_path))
        job_id = text_field(request.get("job_id"), "job_id")
        result["job_id"] = job_id
        for document in (request, cad, verification):
            require_envelope(document, job_id)
        constraints = request.get("constraints", {})
        if not isinstance(constraints, dict) or constraints.get("confirmed") is not True:
            raise PrintError("CONSTRAINTS_UNCONFIRMED", "Confirm the connector/design proposal before slicing.", "needs_input")
        if cad.get("status") != "ok":
            raise PrintError("CAD_NOT_READY", "CAD generation has not completed successfully.", "needs_input")
        if verification.get("status") != "ok" or verification.get("verdict") != "pass":
            raise PrintError("VERIFICATION_NOT_PASSED", "A complete passing verification verdict is required.", "needs_input")
        inputs = verification.get("inputs")
        if not isinstance(inputs, dict):
            raise PrintError("VERIFICATION_PROVENANCE", "Verification must bind the exact request and CAD result hashes.", "needs_input")
        for role in ("request", "cad_result"):
            # The verification worker uses nested file refs. Flat hash fields are
            # accepted for callers of the initial printing contract as well.
            nested = inputs.get(role)
            expected = nested.get("sha256") if isinstance(nested, dict) else inputs.get(role + "_sha256")
            if expected is None:
                raise PrintError("VERIFICATION_PROVENANCE", "Verification must bind the exact request and CAD result hashes.", "needs_input")
            if digest(expected) != source_hashes[role + "_sha256"]:
                raise PrintError("STALE_VERIFICATION", "Verification belongs to different input bytes.")
            if role + "_sha256" in inputs and digest(inputs[role + "_sha256"]) != expected:
                raise PrintError("STALE_VERIFICATION", "Conflicting verification hash bindings.")
        settings = request.get("printing")
        if not isinstance(settings, dict):
            raise PrintError("PRINT_SETTINGS_REQUIRED", "Explicit printer, nozzle, material, bed and full profiles are required.", "needs_input")
        printer = settings.get("printer")
        if not isinstance(printer, dict):
            raise PrintError("PRINTER_REQUIRED", "A printer identity is required.", "needs_input")
        printer_id = text_field(printer.get("id"), "printer.id")
        if printer.get("model") != "Bambu Lab A1":
            raise PrintError("PRINTER_MODEL", "Only Bambu Lab A1 is supported by this preparation adapter.", "unsupported")
        nozzle = positive_number(settings.get("nozzle_diameter_mm"), "nozzle_diameter_mm")
        material = text_field(settings.get("material"), "material")
        bed_type = text_field(settings.get("bed_type"), "bed_type")
        refs = settings.get("profiles")
        if not isinstance(refs, dict) or not all(isinstance(refs.get(role), dict) for role in ("machine", "process", "filament")):
            raise PrintError("PROFILES_REQUIRED", "Pinned machine, process and filament profiles are required.", "needs_input")
        profile_paths = {role: checked_file(refs[role], request_path) for role in ("machine", "process", "filament")}
        normalized = {"nozzle_diameter_mm": nozzle, "material": material, "bed_type": bed_type,
                      "profiles": {role: {"id": text_field(refs[role].get("id"), f"{role}.id"),
                                           "sha256": digest(refs[role].get("sha256"))}
                                   for role in profile_paths}}
        _validate_profiles(profile_paths, normalized)
        cad_artifacts = cad.get("artifacts")
        if not isinstance(cad_artifacts, list) or any(not isinstance(item, dict) for item in cad_artifacts):
            raise PrintError("CAD_ARTIFACTS", "CAD artifacts must be a list of objects.")
        by_kind = {}
        for item in cad_artifacts:
            kind = text_field(item.get("kind"), "artifact.kind")
            if kind in by_kind:
                raise PrintError("DUPLICATE_ARTIFACT", "CAD artifact kinds must be unique.")
            by_kind[kind] = item
        display = constraints.get("display")
        carrier_required = (isinstance(display, dict)
                            and display.get("support_structure") == "removable_pcb_carrier")
        model_kinds = ("base_stl", "lid_stl") + (("pcb_carrier_stl",) if carrier_required else ())
        required = ("base_step", "lid_step", *model_kinds) + (("pcb_carrier_step",) if carrier_required else ())
        if any(kind not in by_kind for kind in required):
            raise PrintError("MESH_REQUIRED", "STEP solids and millimeter STL meshes for every required printable part are required.", "needs_input")
        paths = {}
        for kind in required:
            ref = by_kind[kind]
            if ref.get("units") != "mm":
                raise PrintError("UNITS_REQUIRED", "CAD artifacts must declare millimeter units.", "needs_input")
            paths[kind] = checked_file(ref, cad_result_path)
        protect_output(output_dir, "print_job.json", [*paths.values(), *profile_paths.values()])
        if slicer is None:
            raise PrintError("SLICER_UNAVAILABLE", "Inject a configured real CLI slicer; none is installed by this module.", "unsupported")
        operation_dir = output_dir / ("prepare-" + uuid.uuid4().hex)
        snapshots = operation_dir / "inputs"
        models = [_snapshot(paths[kind], snapshots / f"{kind}.stl", by_kind[kind]["sha256"])
                  for kind in model_kinds]
        copied_profiles = {role: _snapshot(path, snapshots / f"{role}.json", refs[role]["sha256"])
                           for role, path in profile_paths.items()}
        # Validate the copied bytes too, closing the profile read/copy race.
        _validate_profiles(copied_profiles, normalized)
        sliced = slicer.slice(models, copied_profiles, normalized, operation_dir / "slicer")
        if type(sliced.simulation) is not bool:
            raise PrintError("SLICER_MODE", "Slicer must declare whether its output is simulated.")
        if not sliced.simulation and (verification.get("simulation") is True or cad.get("simulation") is True):
            raise PrintError("SIMULATED_VERIFICATION", "Simulated CAD or verification cannot release a live print artifact.", "needs_input")
        for key, path in (("request_sha256", request_path), ("cad_result_sha256", cad_result_path),
                          ("verification_sha256", verification_path)):
            if sha256(path) != source_hashes[key]:
                raise PrintError("INPUT_CHANGED", "An input document changed during slicing.")
        sliced_path = Path(sliced.path).resolve()
        if not sliced_path.is_relative_to(operation_dir) or not sliced_path.is_file():
            raise PrintError("SLICER_OUTPUT_PATH", "Slicer must return a file in the supplied output directory.")
        if not sliced.simulation:
            inspection = inspect_sliced_3mf(sliced_path)
            if len(inspection["gcode_members"]) != 1:
                raise PrintError("MULTIPLE_PLATES", "The enclosure workflow requires one sliced plate.", "needs_input")
            if carrier_required:
                _check_plate_models(sliced_path, models)
        sliced_artifact = artifact("simulation_slice" if sliced.simulation else "sliced_3mf", sliced_path, units="mm")
        result.update({"state": "awaiting_review", "simulation": sliced.simulation,
                       "printer": {"id": printer_id, "model": printer["model"]},
                       "settings": normalized, "sliced_artifact": sliced_artifact,
                       "artifacts": [sliced_artifact], "slicer": sliced.metadata,
                       "inputs": {**source_hashes,
                                  "models": [artifact(kind, path, units="mm")
                                             for kind, path in zip(model_kinds, models)]},
                       "authorization": "A review action bound to this job, printer and final artifact is required."})
    except OutputOverlapError:
        raise
    except PrintError as exc:
        record_error(result, exc)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        record_error(result, PrintError("INVALID_PRINT_INPUT", "Malformed printing input or inaccessible local file."))
    return write_json(output_dir / "print_job.json", result)
