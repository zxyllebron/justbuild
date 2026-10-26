"""Artifact-bound, fail-closed verification of a declared mechanical scope."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import tempfile


CORE_SCOPES = ("board_geometry", "intentional_contacts", "clearances", "mounting_holes",
               "ports", "keepouts", "assembly_paths")
UNIMPLEMENTED = ("wall_thickness", "cable_bend", "thermal", "rf", "strength",
                 "nonlinear_assembly")
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


class InputError(ValueError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _object(value, label):
    if not isinstance(value, dict):
        raise InputError(f"{label} must be an object")
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise InputError(f"{label} must be a list")
    return value


def _string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} must be a nonempty string")
    return value


def _number(value, label, minimum=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(f"{label} must be finite numeric data")
    if minimum is not None and value < minimum or positive and value <= 0:
        raise InputError(f"{label} is outside its permitted range")
    return float(value)


def _vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise InputError(f"{label} must contain three millimeter coordinates")
    return [_number(v, label) for v in value]


def _unit(value, label):
    vector = _vector(value, label)
    if abs(math.sqrt(sum(v * v for v in vector)) - 1) > 1e-8:
        raise InputError(f"{label} must be a unit vector")
    return vector


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _matrix(value):
    if not isinstance(value, list) or len(value) != 4:
        raise InputError("board_transform must be a rigid 4x4 matrix")
    rows = []
    for row in value:
        if not isinstance(row, list) or len(row) != 4:
            raise InputError("board_transform must be a rigid 4x4 matrix")
        rows.append([_number(x, "board_transform") for x in row])
    if rows[3] != [0, 0, 0, 1]:
        raise InputError("board_transform last row must be [0,0,0,1]")
    axes = [row[:3] for row in rows[:3]]
    for i, a in enumerate(axes):
        for j, b in enumerate(axes):
            if abs(_dot(a, b) - int(i == j)) > 1e-8:
                raise InputError("board_transform must not scale or skew geometry")
    a, b, c = axes
    determinant = (a[0] * (b[1] * c[2] - b[2] * c[1])
                   - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if abs(determinant - 1) > 1e-8:
        raise InputError("board_transform must preserve handedness")
    return rows


def _read(path):
    def reject(value):
        raise InputError(f"Nonfinite JSON token: {value}")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise InputError(f"Duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise InputError("JSON number exceeds the finite floating-point range")
        return number

    raw = path.read_bytes()
    document = _object(json.loads(raw.decode("utf-8"), parse_constant=reject,
                                  parse_float=finite_float, object_pairs_hook=unique), str(path))
    return document, hashlib.sha256(raw).hexdigest()


def _source(value, label):
    return _string(value, f"{label}.provenance")


class Verifier:
    def __init__(self, request_path, cad_path):
        self.request_path, self.cad_path = request_path, cad_path
        self.result = {
            "schema_version": "0.1", "job_id": "unknown", "status": "needs_input",
            "verdict": "incomplete", "artifacts": [], "issues": [], "checks": [],
            "repair_proposals": [], "checked_geometry": [], "inputs": {},
            "scope": {"required": list(CORE_SCOPES), "unimplemented": list(UNIMPLEMENTED)},
        }
        self.parts = {}
        self.tol = 1e-5
        self.vtol = 1e-7
        self.kernel = None

    def add(self, check_id, category, status, method, measurements=None,
            code=None, message=None, feature_id=None, required=True, repair=None):
        record = {"id": check_id, "category": category, "status": status,
                  "required": required, "method": method,
                  "tolerance_mm": self.tol, "volume_tolerance_mm3": self.vtol,
                  "measurements": measurements or {}}
        if feature_id is not None:
            record["feature_id"] = feature_id
        self.result["checks"].append(record)
        if code:
            issue = {"code": code, "severity": "error" if status == "fail" else "warning",
                     "message": message, "check_id": check_id,
                     "evidence": record["measurements"]}
            if feature_id is not None:
                issue["feature_id"] = feature_id
            self.result["issues"].append(issue)
        if repair and status == "fail":
            numeric_bounds = {}
            if measurements and "deficit_mm" in measurements:
                numeric_bounds["minimum_separation_increase_mm"] = measurements["deficit_mm"] + self.tol
            if measurements and "minimum_diameter_mm" in measurements:
                numeric_bounds.update({k: measurements[k] for k in (
                    "reference_center_mm", "reference_axis", "required_span_mm",
                    "minimum_diameter_mm", "maximum_diameter_mm", "position_tolerance_mm")})
            self.result["repair_proposals"].append({
                "id": f"repair:{check_id}", "check_id": check_id,
                "feature_id": feature_id, "action": repair,
                "auto_apply": False, "requires_reverification": True,
                "preserve": ["confirmed clearances", "required openings",
                             "PCB mounting-hole centers", "board placement"],
                "bounds": {"editable_parts": ["base", "lid"] + (["pcb_carrier"] if "pcb_carrier" in self.parts else []),
                           "max_attempts": 3,
                           "new_constraint_confirmation_if_bounds_cannot_be_met": True,
                           **numeric_bounds},
                "requires_feasibility_checks": ["confirmed shell dimensions", "minimum wall thickness",
                                                "fastener support and remaining required checks"],
            })

    def guarded(self, check_id, category, function, feature_id=None):
        try:
            function()
        except (InputError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.add(check_id, category, "not_checked", "input_or_kernel_error",
                     {"reason": str(exc), "exception_type": type(exc).__name__},
                     "CHECK_UNAVAILABLE", "Required check could not be completed.", feature_id)

    def coverage(self, name):
        declaration = _object(self.options.get("coverage", {}).get(name, {}), f"coverage.{name}")
        complete = declaration.get("complete") is True and bool(declaration.get("provenance"))
        if complete:
            _source(declaration.get("provenance"), name)
        self.add(f"coverage:{name}", name, "pass" if complete else "not_checked",
                 "caller_declaration_of_requirement_inventory",
                 {"declaration": declaration},
                 None if complete else "REQUIREMENTS_INCOMPLETE",
                 None if complete else f"{name} inventory is not confirmed complete with provenance.")
        return complete

    def run(self):
        self.request, request_hash = _read(self.request_path)
        self.result["job_id"] = _string(self.request.get("job_id"), "job_id")
        self.cad, cad_hash = _read(self.cad_path)
        self.result["inputs"] = {
            "request": {"path": str(self.request_path), "sha256": request_hash},
            "cad_result": {"path": str(self.cad_path), "sha256": cad_hash},
        }
        if self.request.get("schema_version") != "0.1" or self.cad.get("schema_version") != "0.1":
            raise InputError("Both input schemas must be version 0.1")
        if self.cad.get("job_id") != self.result["job_id"]:
            raise InputError("CAD result job_id differs from request")
        if self.cad.get("status") != "ok":
            raise InputError("CAD result must have status ok before verification")
        self.constraints = _object(self.request.get("constraints"), "constraints")
        if self.constraints.get("confirmed") is not True:
            raise InputError("Constraints require explicit user confirmation")
        if self.constraints.get("structure") != "single_pcb_screw_lid":
            raise InputError("Only single_pcb_screw_lid is supported")
        self.options = _object(self.request.get("verification", {}), "verification")
        self.tol = _number(self.options.get("tolerance_mm", 1e-5), "tolerance_mm", positive=True)
        self.vtol = _number(self.options.get("volume_tolerance_mm3", 1e-7),
                            "volume_tolerance_mm3", positive=True)
        # Excessive numerical tolerances could disguise violations of hard constraints.
        if self.tol > 0.001 or self.vtol > 0.001:
            raise InputError("Numerical tolerances exceed supported maximum 0.001 mm / mm3")
        self.result["numerical_policy"] = {
            "units": "mm", "tolerance_mm": self.tol, "volume_tolerance_mm3": self.vtol,
            "angular_numeric_tolerance_deg": 1e-6,
            "full_circular_face_tolerance_rad": 1e-6,
            "source": "explicit verification options or documented kernel defaults",
            "clearance_comparison": "measured + tolerance_mm >= required",
        }
        _object(self.options.get("coverage", {}), "coverage")
        for scope in CORE_SCOPES:
            self.coverage(scope)
        required_extra = _list(self.options.get("required_checks", []), "required_checks")
        required_extra = [_string(name, "required_checks entry") for name in required_extra]
        for scope in sorted(set(UNIMPLEMENTED) | set(required_extra)):
            if scope in CORE_SCOPES:
                continue
            self.add(f"unimplemented:{scope}", scope, "not_checked", "unimplemented",
                     {"reason": "No implemented physical or geometric analysis for this scope."},
                     "ANALYSIS_NOT_IMPLEMENTED", "This analysis has not been performed.",
                     required=scope in required_extra)
        try:
            from . import geometry
        except ImportError as exc:
            self.add("geometry_backend", "geometry", "not_checked", "dependency_import",
                     {"reason": str(exc)}, "BACKEND_UNAVAILABLE", "Install documented CadQuery dependencies.")
            self.result["status"] = "unsupported"
            return
        self.kernel = geometry
        self.result["backend"] = {"name": "CadQuery/OpenCascade", "cadquery_version": geometry.cq.__version__}
        self.guarded("solid:board", "solids", self.load_board)
        artifacts = _list(self.cad.get("artifacts"), "cad.artifacts")
        declared_parts = ["base", "lid"]
        display = self.constraints.get("display", {})
        carrier_selected = isinstance(display, dict) and display.get("support_structure") == "removable_pcb_carrier"
        carrier_artifacts = [a for a in artifacts if isinstance(a, dict) and a.get("kind") == "pcb_carrier_step"]
        if carrier_selected:
            declared_parts += ["pcb_carrier", "screen"]
        elif carrier_artifacts:
            self.add("undeclared:pcb_carrier", "geometry", "not_checked", "explicit_additional_part_gate",
                     {"reason": "A pcb_carrier artifact requires display.support_structure=removable_pcb_carrier"},
                     "ADDITIONAL_PART_UNDECLARED", "Do not omit an exported carrier from independent checks.")
        for part in declared_parts:
            self.guarded(f"solid:{part}", "solids", lambda p=part: self.load_artifact(p, artifacts))
        self.guarded("coordinate_frame", "geometry", self.validate_frame)
        if set(self.parts) != {"board", *declared_parts} or not self.frame_valid:
            self.add("dependent_checks", "geometry", "not_checked", "prerequisite_gate",
                     {"valid_parts": sorted(self.parts), "frame_valid": self.frame_valid},
                     "GEOMETRY_PREREQUISITES_MISSING", "All declared valid parts and a common frame are required.")
            return
        self.guarded("collisions", "collision", self.collisions)
        self.guarded("clearances", "clearances", self.clearances)
        self.guarded("mounting_holes", "mounting_holes", self.mountings)
        self.guarded("ports", "ports", self.ports)
        self.guarded("keepouts", "keepouts", self.keepouts)
        self.guarded("assembly_paths", "assembly_paths", self.assembly_paths)
        # Detect changed artifacts; never publish a pass for different bytes than those imported.
        for record in self.result["checked_geometry"]:
            if digest(Path(record["path"])) != record["sha256"]:
                self.add(f"stable:{record['part_id']}", "provenance", "fail", "sha256_readback",
                         record, "INPUT_CHANGED", "Geometry changed while it was being checked.")
        for record in self.result["inputs"].values():
            if digest(Path(record["path"])) != record["sha256"]:
                self.add("stable:input", "provenance", "fail", "sha256_readback", record,
                         "INPUT_CHANGED", "An input document changed while it was being checked.")

    frame_valid = False

    def validate_frame(self):
        frame = _object(self.constraints.get("board_frame"), "board_frame")
        _vector(frame.get("origin_mm"), "board_frame.origin_mm")
        axes = [_unit(frame.get(f"{axis}_axis"), f"board_frame.{axis}_axis") for axis in "xyz"]
        _matrix([[*axis, 0] for axis in axes] + [[0, 0, 0, 1]])
        if self.options.get("geometry_frame") != "confirmed_board_frame":
            raise InputError("Declare verification.geometry_frame=confirmed_board_frame; all artifacts/envelopes must share it")
        self.frame_valid = True
        self.add("coordinate_frame", "geometry", "pass", "explicit_common_frame_declaration",
                 {"board_frame": frame, "board_transform": self.options.get("board_transform", IDENTITY)})

    def register(self, part, path, expected_hash=None, matrix=None):
        if path.suffix.lower() not in (".step", ".stp"):
            raise InputError("Authoritative geometry must be STEP/STP")
        actual = digest(path)
        if expected_hash is not None and actual != expected_hash:
            self.add(f"hash:{part}", "provenance", "fail", "sha256",
                     {"path": str(path), "expected_sha256": expected_hash, "actual_sha256": actual},
                     "ARTIFACT_HASH_MISMATCH", "STEP bytes do not match the artifact manifest.", part)
            return
        record = {"part_id": part, "path": str(path), "sha256": actual, "units": "mm"}
        self.result["checked_geometry"].append(record)
        shape = self.kernel.load_step(path)
        if matrix is not None:
            shape = self.kernel.transform(shape, matrix)
            record["source_to_board_transform"] = matrix
        measured = self.kernel.validity(shape, self.vtol)
        self.add(f"solid:{part}", "solids", "pass" if measured["valid"] else "fail",
                 "OCC_BRep_validation_and_positive_solid_volumes", measured,
                 None if measured["valid"] else "INVALID_SOLID",
                 None if measured["valid"] else "Input is not exclusively valid positive-volume CAD solids.", part)
        if measured["valid"]:
            self.parts[part] = shape

    def load_board(self):
        board = _object(self.request.get("board"), "board")
        if board.get("units") != "mm":
            raise InputError("Board declared units must be mm")
        path = Path(_string(board.get("step_path"), "board.step_path"))
        path = (self.request_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
        self.register("board", path, board.get("sha256"), _matrix(self.options.get("board_transform", IDENTITY)))

    def load_artifact(self, part, artifacts):
        kind = "screen_envelope_step" if part == "screen" else f"{part}_step"
        matches = [a for a in artifacts if isinstance(a, dict) and a.get("kind") == kind]
        if len(matches) != 1:
            raise InputError(f"Exactly one {part}_step artifact is required")
        artifact = matches[0]
        if artifact.get("units") != "mm":
            raise InputError(f"{part} STEP declared units must be mm")
        expected = _string(artifact.get("sha256"), "artifact.sha256")
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise InputError("artifact.sha256 must be lowercase SHA-256 hex")
        path = Path(_string(artifact.get("path"), "artifact.path"))
        path = (self.cad_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
        self.register(part, path, expected)

    def pair(self, value):
        pair = _list(value, "parts")
        if len(pair) != 2 or pair[0] == pair[1] or any(p not in self.parts for p in pair):
            raise InputError("parts must name two different loaded assembly parts")
        return tuple(sorted(pair))

    def collisions(self):
        contacts = {}
        for entry in _list(self.options.get("intentional_contacts", []), "intentional_contacts"):
            _object(entry, "contact")
            pair = self.pair(entry.get("parts"))
            _source(entry.get("provenance"), "contact")
            _string(entry.get("reason"), "contact.reason")
            if entry.get("mode") != "touch_only":
                raise InputError("Intentional contacts only support touch_only; positive-volume overlap is never exempt")
            if pair in contacts:
                raise InputError("Duplicate intentional contact pair")
            regions = _list(entry.get("regions"), "contact.regions")
            if not regions:
                raise InputError("Touch exemptions require spatially bounded contact regions")
            for region in regions:
                self.envelope(region)
            contacts[pair] = entry
        for a, b in itertools.combinations(sorted(self.parts), 2):
            common_evidence = {}
            volume = self.kernel.intersection_volume(self.parts[a], self.parts[b], details=common_evidence)
            measured = self.kernel.distance(self.parts[a], self.parts[b])
            measured.update({"parts": [a, b], "intersection_volume_mm3": volume,
                             "intersection_evidence": common_evidence,
                             "intentional_contact": contacts.get((a, b))})
            intersecting = volume > self.vtol
            touching = measured["minimum_distance_mm"] <= self.tol
            unbounded_contact = False
            if touching and (a, b) in contacts and not intersecting:
                regions = [self.envelope(r)[0] for r in contacts[(a, b)]["regions"]]
                outside = self.kernel.distances_outside_regions(self.parts[a], self.parts[b], regions)
                measured["distances_outside_contact_regions"] = outside
                unbounded_contact = any(m["minimum_distance_mm"] <= self.tol for m in outside)
            failed = intersecting or touching and (a, b) not in contacts or unbounded_contact
            self.add(f"collision:{a}:{b}", "collision", "fail" if failed else "pass",
                 "OCC_per_solid_Common_volume_sum_and_BRep_minimum_distance", measured,
                     "SOLID_INTERSECTION" if intersecting else "UNDECLARED_CONTACT_LOCATION" if unbounded_contact
                     else "UNINTENDED_CONTACT" if failed else None,
                     "Unintended material overlap or undeclared contact." if failed else None,
                     f"{a}:{b}", repair="Remove intersecting shell material or enlarge the shell within confirmed dimensions; preserve PCB placement and all hard constraints.")

    def clearances(self):
        entries = _list(self.options.get("clearances", []), "clearances")
        for index, entry in enumerate(entries):
            self.guarded(f"clearance:{index}", "clearances", lambda e=entry, i=index: self.clearance(e, i))
        if not entries:
            self.empty("clearances")

    def clearance(self, entry, index):
        _object(entry, "clearance")
        a, b = self.pair(entry.get("parts"))
        minimum = _number(entry.get("minimum_mm"), "clearance.minimum_mm", minimum=0)
        _source(entry.get("provenance"), "clearance")
        measured = self.kernel.distance(self.parts[a], self.parts[b])
        measured.update({"parts": [a, b], "required_minimum_mm": minimum,
                         "deficit_mm": max(0.0, minimum - measured["minimum_distance_mm"])})
        passed = measured["minimum_distance_mm"] + self.tol >= minimum
        self.add(f"clearance:{index}", "clearances", "pass" if passed else "fail",
                 "OCC_BRep_minimum_distance", measured, None if passed else "CLEARANCE_TOO_SMALL",
                 None if passed else "Measured minimum clearance is below the confirmed requirement.",
                 f"{a}:{b}", repair=f"Increase shell-side separation by at least the reported deficit plus the numerical tolerance ({self.tol} mm); retain the required minimum {minimum} mm.")

    def inventory(self, key):
        entries = _list(self.constraints.get(key), f"constraints.{key}")
        found = {}
        for entry in entries:
            _object(entry, key)
            identity = _string(entry.get("id"), f"{key}.id")
            if identity in found:
                raise InputError(f"Duplicate {key} id: {identity}")
            found[identity] = entry
        return found

    def empty(self, scope):
        declaration = self.options.get("coverage", {}).get(scope, {})
        known = declaration.get("complete") is True and bool(declaration.get("provenance"))
        self.add(f"inventory:{scope}", scope, "not_applicable" if known else "not_checked",
                 "explicit_empty_requirement_inventory" if known else "missing_requirement_inventory",
                 {"declaration": declaration})

    def mountings(self):
        holes = self.inventory("mounting_holes")
        targets = _object(self.options.get("mounting_checks", {}), "mounting_checks")
        if set(targets) - set(holes):
            raise InputError("mounting_checks references an unknown mounting-hole id")
        if not holes:
            self.empty("mounting_holes")
        for identity, hole in holes.items():
            self.guarded(f"mounting:{identity}", "mounting_holes",
                         lambda i=identity, h=hole: self.mounting(i, h, targets.get(i)), identity)

    def mounting(self, identity, hole, targets):
        center = _vector(hole.get("center_mm"), "hole.center_mm")
        axis = _unit(hole.get("axis"), "hole.axis")
        reference_diameter = _number(hole.get("diameter_mm"), "hole.diameter_mm", positive=True)
        _source(hole.get("provenance"), "hole")
        reference_position_tol = _number(hole.get("position_tolerance_mm", 0),
                                         "hole.position_tolerance_mm", minimum=0)
        reference_diameter_tol = _number(hole.get("diameter_tolerance_mm", 0),
                                         "hole.diameter_tolerance_mm", minimum=0)
        board_solids = self.parts["board"].Solids()
        metadata = _object(self.request["board"].get("metadata", {}), "board.metadata")
        selected = metadata.get("pcb_solid_index")
        if selected is None and len(board_solids) == 1:
            selected = 0
            selection_source = "the source contains exactly one solid"
        else:
            if isinstance(selected, bool) or not isinstance(selected, int) or not 0 <= selected < len(board_solids):
                raise InputError("A multi-solid PCB assembly requires an explicit valid metadata.pcb_solid_index")
            selection_source = _source(metadata.get("pcb_selection_source"), "PCB solid selection")
        reference_candidates = self.kernel.bore_candidates(board_solids[selected], center, axis, self.tol)
        reference_aligned = [c for c in reference_candidates
                    if c["axis_offset_mm"] <= reference_position_tol + self.tol
                    and c["axis_angle_deg"] <= 1e-6
                    and abs(c["diameter_mm"] - reference_diameter) <= reference_diameter_tol + self.tol]
        matching = [c for c in reference_aligned if c["full_circular_face"]]
        unsupported_topology = bool(reference_aligned) and not matching
        self.add(f"mounting:{identity}:board_datum", "mounting_holes",
                 "pass" if matching else "not_checked" if unsupported_topology else "fail",
                 "OCC_PCB_internal_cylindrical_surface_datum_with_analytic_angular_union",
                 {"reference_center_mm": center, "reference_axis": axis,
                  "pcb_solid_index": selected, "pcb_selection_source": selection_source,
                  "source_sha256": next(g["sha256"] for g in self.result["checked_geometry"] if g["part_id"] == "board"),
                  "reference_diameter_mm": reference_diameter,
                  "position_tolerance_mm": reference_position_tol,
                  "diameter_tolerance_mm": reference_diameter_tol,
                  "candidate_bores": reference_candidates, "matching_bores": matching},
                 None if matching else "UNSUPPORTED_BORE_TOPOLOGY" if unsupported_topology
                 else "PCB_MOUNTING_DATUM_MISMATCH",
                 None if matching else "Measured cylindrical patches do not prove a full circumference over one common axial interval."
                 if unsupported_topology else "Confirmed mounting datum does not match an actual cylindrical cavity in the PCB assembly.",
                 identity)
        targets = _list(targets, "mounting_checks[hole_id]")
        if not targets:
            raise InputError("Each mounting hole needs at least one generated-part bore requirement")
        for index, target in enumerate(targets):
            self.guarded(f"mounting:{identity}:{index}", "mounting_holes",
                         lambda t=target, n=index: self.bore(identity, center, axis, t, n), identity)

    def bore(self, identity, center, axis, target, index):
        _object(target, "bore")
        part = target.get("part_id")
        if part not in ("base", "lid", "pcb_carrier") or part not in self.parts:
            raise InputError("Mounting target must be a loaded base, lid or pcb_carrier")
        span = _list(target.get("span_mm"), "bore.span_mm")
        if len(span) != 2:
            raise InputError("bore.span_mm needs two offsets along the confirmed hole axis")
        span = [_number(v, "span_mm") for v in span]
        if span[1] <= span[0]:
            raise InputError("bore.span_mm must be increasing")
        diameter = _number(target.get("minimum_diameter_mm"), "minimum_diameter_mm", positive=True)
        position_tol = _number(target.get("position_tolerance_mm"), "position_tolerance_mm", minimum=0)
        angle_tol = _number(target.get("angle_tolerance_deg"), "angle_tolerance_deg", minimum=0)
        maximum = target.get("maximum_diameter_mm")
        if maximum is not None:
            maximum = _number(maximum, "maximum_diameter_mm", minimum=diameter)
        _source(target.get("provenance"), "bore")
        candidates = self.kernel.bore_candidates(self.parts[part], center, axis, self.tol)
        eligible = [c for c in candidates if c["axis_offset_mm"] <= position_tol + self.tol
                    and c["axis_angle_deg"] <= angle_tol + 1e-6
                    and c["diameter_mm"] + self.tol >= diameter
                    and (maximum is None or c["diameter_mm"] <= maximum + self.tol)
                    and c["span_mm"][0] <= span[0] + self.tol
                    and c["span_mm"][1] >= span[1] - self.tol]
        matching = [c for c in eligible if c["full_circular_face"]]
        # A void probe alone could pass outside a part. A recognized bore and its
        # axial span must also match the confirmed PCB mounting datum.
        probe = self.kernel.cylinder(center, axis, diameter, span)
        common_evidence = {}
        volume = self.kernel.intersection_volume(probe, self.parts[part], details=common_evidence)
        passed = bool(matching) and volume <= self.vtol
        unsupported_topology = bool(eligible) and not matching and volume <= self.vtol
        measured = {"part_id": part, "reference_center_mm": center, "reference_axis": axis,
                    "required_span_mm": span, "minimum_diameter_mm": diameter,
                    "maximum_diameter_mm": maximum, "position_tolerance_mm": position_tol,
                    "angle_tolerance_deg": angle_tol, "blocking_volume_mm3": volume,
                    "intersection_evidence": common_evidence,
                    "matching_bores": matching, "candidate_bores": candidates}
        self.add(f"mounting:{identity}:{index}", "mounting_holes",
                 "pass" if passed else "not_checked" if unsupported_topology else "fail",
                 "OCC_internal_cylindrical_angular_union_plus_full_cylinder_void_probe", measured,
                 None if passed else "UNSUPPORTED_BORE_TOPOLOGY" if unsupported_topology
                 else "MOUNTING_BORE_MISMATCH",
                 None if passed else "Measured cylindrical patches do not prove a full circumference over one common axial interval."
                 if unsupported_topology else "No unobstructed cylindrical bore meets the confirmed axis, diameter and span.",
                 identity, repair="Correct the generated shell bore to the existing PCB datum and required bore interval/diameter; do not relocate the PCB hole. Reconfirm requirements if the shell cannot accommodate it.")

    def envelope(self, spec):
        spec = _object(spec, "envelope")
        _source(spec.get("provenance"), "envelope")
        if spec.get("kind") != "box":
            raise InputError("Only explicitly specified axis-aligned box envelopes are currently supported")
        low = _vector(spec.get("min_mm"), "envelope.min_mm")
        high = _vector(spec.get("max_mm"), "envelope.max_mm")
        if any(b <= a for a, b in zip(low, high)):
            raise InputError("Envelope box must have positive dimensions")
        return self.kernel.box(low, high), low, high

    def obstacles(self, entry):
        against = _list(entry.get("against"), "against")
        if not against or len(set(against)) != len(against) or any(p not in self.parts for p in against):
            raise InputError("against must name distinct loaded obstacle parts")
        return against

    def empty_envelope(self, check_id, category, feature_id, entry, shape, extra=None,
                       enforce_contact_policy=False):
        margin = _number(entry.get("minimum_clearance_mm"), "minimum_clearance_mm", minimum=0)
        for obstacle in self.obstacles(entry):
            common_evidence = {}
            volume = self.kernel.intersection_volume(shape, self.parts[obstacle], details=common_evidence)
            bound = self.kernel.separation_lower_bound(shape, self.parts[obstacle])
            proven_separate = (volume == 0.0 and
                               bound["minimum_distance_lower_bound_mm"] > max(margin, self.tol))
            if proven_separate:
                measured = {**bound, "exact_distance_solver_skipped": True,
                            "comparison_policy": "lower_bound strictly greater than required clearance and contact tolerance"}
                passed = True
            else:
                measured = self.kernel.distance(shape, self.parts[obstacle])
                passed = volume <= self.vtol and measured["minimum_distance_mm"] + self.tol >= margin
            measured.update({"obstacle_part": obstacle, "intersection_volume_mm3": volume,
                             "intersection_evidence": common_evidence,
                             "required_minimum_mm": margin, **(extra or {})})
            if passed and enforce_contact_policy and not proven_separate and measured["minimum_distance_mm"] <= self.tol:
                pair = sorted([entry["moving_part"], obstacle])
                declared = [contact for contact in self.options.get("intentional_contacts", [])
                            if sorted(contact.get("parts", [])) == pair]
                if len(declared) != 1 or declared[0].get("mode") != "touch_only":
                    passed = False
                    measured["path_contact_policy"] = "contact_has_no_unique_touch_only_declaration"
                else:
                    regions = [self.envelope(region)[0] for region in declared[0]["regions"]]
                    outside = self.kernel.distances_outside_regions(shape, self.parts[obstacle], regions)
                    measured["distances_outside_contact_regions"] = outside
                    passed = all(item["minimum_distance_mm"] > self.tol for item in outside)
                    measured["path_contact_policy"] = "touch_must_stay_within_declared_contact_regions"
            self.add(f"{check_id}:{obstacle}", category, "pass" if passed else "fail",
                     ("OCC_per_solid_Common_sum_and_conservative_BRep_AABB_distance_lower_bound" if proven_separate
                      else "OCC_per_solid_Common_sum_and_distance_of_declared_solid_envelope"), measured,
                     None if passed else "REQUIRED_ENVELOPE_OBSTRUCTED",
                     None if passed else "The declared required empty envelope or its clearance is obstructed.",
                     feature_id, repair="Remove shell obstruction or enlarge the required opening within confirmed shell constraints. Preserve envelope dimensions and clearances; request revised constraints if no bounded repair exists.")

    def ports(self):
        ports = self.inventory("ports")
        entries = _object(self.options.get("port_checks", {}), "port_checks")
        if set(entries) - set(ports):
            raise InputError("port_checks references an unknown port id")
        if not ports:
            self.empty("ports")
        for identity, port in ports.items():
            self.guarded(f"port:{identity}", "ports",
                         lambda i=identity, p=port: self.port(i, p, entries.get(i)), identity)

    def port(self, identity, port, entry):
        mode = port.get("access_mode")
        if mode == "covered":
            self.add(f"opening:{identity}", "ports", "not_applicable", "confirmed_access_mode",
                     {"access_mode": mode}, feature_id=identity)
            return
        if mode not in ("external", "maintenance", "internal"):
            raise InputError("Port access_mode must be external/internal/maintenance/covered")
        entry = _object(entry, "port_checks[port_id]")
        if mode == "internal":
            self.add(f"opening:{identity}", "ports", "not_applicable", "confirmed_access_mode",
                     {"access_mode": mode}, feature_id=identity)
        else:
            if not (set(self.parts) - {"board"}).issubset(self.obstacles(entry)):
                raise InputError("Required opening checks must include all declared shell and screen parts")
            shape, _, _ = self.envelope(entry.get("opening_envelope"))
            self.empty_envelope(f"opening:{identity}", "ports", identity, entry, shape,
                                {"opening_envelope": entry["opening_envelope"]})
        insertion = _object(entry.get("insertion"), "port.insertion")
        self.path_obstacles(insertion, set(self.parts))
        # Path translation is from connected pose to withdrawn pose; the
        # insertion direction supplied by the board contract points inward.
        direction = _unit(port.get("insertion_direction"), "port.insertion_direction")
        translation = _vector(insertion.get("translation_mm"), "insertion.translation_mm")
        length = math.sqrt(_dot(translation, translation))
        if length <= self.tol or _dot(direction, translation) / length > -1 + 1e-8:
            raise InputError("Withdrawal translation must oppose the confirmed insertion direction")
        self.path(f"insertion:{identity}", "ports", identity, insertion)

    def keepouts(self):
        entries = self.inventory("keepouts")
        if not entries:
            self.empty("keepouts")
        for identity, entry in entries.items():
            self.guarded(f"keepout:{identity}", "keepouts",
                         lambda i=identity, e=entry: self.keepout(i, e), identity)

    def keepout(self, identity, entry):
        _source(entry.get("provenance"), "keepout")
        _string(entry.get("purpose"), "keepout.purpose")
        if not (set(self.parts) - {"board"}).issubset(self.obstacles(entry)):
            raise InputError("Shell keepouts must check all declared shell and screen parts")
        shape, _, _ = self.envelope(entry.get("geometry"))
        self.empty_envelope(f"keepout:{identity}", "keepouts", identity, entry, shape,
                            {"required_geometry": entry["geometry"]})

    def assembly_paths(self):
        entries = _list(self.options.get("assembly_checks", []), "assembly_checks")
        identities = set()
        moving_parts = {e.get("moving_part") for e in entries if isinstance(e, dict)}
        required_parts = ["board", "lid"] + (["screen", "pcb_carrier"] if "pcb_carrier" in self.parts else [])
        for required_part in required_parts:
            if required_part not in moving_parts:
                self.add(f"assembly:missing:{required_part}", "assembly_paths", "not_checked",
                         "single_pcb_screw_lid_required_path_inventory", {"moving_part": required_part},
                         "ASSEMBLY_PATH_MISSING", "Every moving PCB, lid, screen and removable carrier requires a declared assembly/removal path.")
        for index, entry in enumerate(entries):
            _object(entry, "assembly_check")
            identity = _string(entry.get("id"), "assembly_check.id")
            if identity in identities:
                raise InputError("Duplicate assembly path id")
            identities.add(identity)
            self.guarded(f"assembly:{identity}", "assembly_paths",
                         lambda i=identity, e=entry: self.assembly_path(i, e), identity)

    def assembly_path(self, identity, entry):
        if entry.get("moving_part") not in self.parts:
            raise InputError("Assembly path must identify its actual moving_part")
        self.path_obstacles(entry, set(self.parts) - {entry["moving_part"]})
        if "pcb_carrier" in self.parts:
            # This explicit mechanical structure supports one reviewed order.
            # Independently declaring other accessories absent in every path
            # cannot establish that the final combined assembly is reachable.
            order = ["screen", "pcb_carrier", "board", "lid"]
            stage = order.index(entry["moving_part"]) if entry["moving_part"] in order else -1
            if stage < 0:
                raise InputError("Removable carrier assembly moves screen, carrier, board or lid only")
            expected_present = {"base", *order[:stage]}
            expected_absent = set(order[stage + 1:])
            if set(self.obstacles(entry)) != expected_present or set(entry.get("absent_parts", [])) != expected_absent:
                raise InputError("Removable carrier paths require the reviewed screen -> pcb_carrier -> board -> lid assembly order")
        self.path(f"assembly:{identity}", "assembly_paths", identity, entry)

    def path_obstacles(self, entry, expected):
        against = set(self.obstacles(entry))
        absent = _list(entry.get("absent_parts", []), "path.absent_parts")
        if len(set(absent)) != len(absent) or any(p not in expected for p in absent):
            raise InputError("absent_parts must identify distinct otherwise-present obstacle parts")
        if absent:
            _source(entry.get("stage_provenance"), "path.stage")
        if against & set(absent) or against | set(absent) != expected:
            raise InputError("Path obstacles plus explicitly absent stage parts must cover every other assembly part")

    def path(self, check_id, category, feature_id, entry):
        _source(entry.get("provenance"), "path")
        envelope = _object(entry.get("envelope"), "path.envelope")
        if envelope.get("kind") == "boxes":
            return self.boxes_path(check_id, category, feature_id, entry)
        if envelope.get("kind") == "moving_part_brep":
            return self.brep_path(check_id, category, feature_id, entry)
        shape, low, high = self.envelope(entry.get("envelope"))
        translation = _vector(entry.get("translation_mm"), "path.translation_mm")
        if sum(v != 0 for v in translation) != 1 or max(abs(v) for v in translation) <= self.tol:
            raise InputError("Only nonzero box-axis-aligned rigid linear paths are supported; no rotations or sampling")
        moving = entry.get("moving_part")
        if moving is not None:
            if moving not in self.parts or moving in self.obstacles(entry):
                raise InputError("moving_part must be loaded and distinct from obstacles")
            outside = self.kernel.difference_volume(self.parts[moving], shape)
            self.add(f"{check_id}:containment", category,
                     "pass" if outside <= self.vtol else "fail", "OCC_per_solid_moving_part_minus_declared_envelope_volume_sum",
                     {"moving_part": moving, "outside_envelope_volume_mm3": outside,
                      "envelope": entry["envelope"]}, None if outside <= self.vtol else "MOVING_PART_OUTSIDE_ENVELOPE",
                     None if outside <= self.vtol else "The declared path envelope does not contain the actual moving solid.",
                     feature_id, repair="Supply a confirmed envelope that contains the moving part; do not shrink or omit the moving solid to clear an obstacle.")
        swept_low = [min(a, a + t) for a, t in zip(low, translation)]
        swept_high = [max(b, b + t) for b, t in zip(high, translation)]
        swept = self.kernel.box(swept_low, swept_high)
        self.empty_envelope(check_id, category, feature_id, entry, swept,
                            {"sweep_method": "exact_continuous_union_of_declared_box_translations",
                             "start_envelope": entry["envelope"], "translation_mm": translation,
                             "swept_box_min_mm": swept_low, "swept_box_max_mm": swept_high,
                             "limitation": "Conservative declared envelope; no rotation, cable deformation, or nonlinear path proof."})

    def brep_path(self, check_id, category, feature_id, entry):
        """Retain real moving-part holes while checking a continuous translation."""
        moving = entry.get("moving_part")
        if category != "assembly_paths" or moving not in self.parts or moving in self.obstacles(entry):
            raise InputError("moving_part_brep is supported only for a loaded assembly moving_part")
        envelope = _object(entry.get("envelope"), "path.envelope")
        _source(envelope.get("provenance"), "moving_part_brep envelope")
        translation = _vector(entry.get("translation_mm"), "path.translation_mm")
        if sum(v != 0 for v in translation) != 1 or max(abs(v) for v in translation) <= self.tol:
            raise InputError("Only nonzero axis-aligned rigid linear BRep paths are supported")
        records = [record for record in self.result["checked_geometry"] if record["part_id"] == moving]
        if len(records) != 1:
            raise InputError("Actual moving geometry needs one hash-bound imported STEP artifact")
        swept, evidence = self.kernel.linear_brep_sweep(self.parts[moving], translation)
        self.add(f"{check_id}:containment", category, "pass", "actual_hash_bound_moving_BRep_no_proxy_envelope",
                 {"moving_part": moving, "checked_geometry": records[0],
                  "source_solid_count": evidence["source_solid_count"]}, feature_id=feature_id)
        self.empty_envelope(check_id, category, feature_id, entry, swept,
                            {"sweep_method": "continuous_union_of_actual_BRep_and_analytic_boundary_face_prisms",
                             "translation_mm": translation, "sweep_evidence": evidence,
                             "limitation": "Rigid single-axis translation; unsupported curved faces fail closed. No flexible or nonlinear assembly proof."},
                            enforce_contact_policy=True)

    def boxes_path(self, check_id, category, feature_id, entry):
        """Continuous union of explicit boxes; every actual solid is covered.

        Each swept box is checked independently: overlapping boxes are never
        used as an artificial compound volume or as a source-volume proof.
        """
        moving = entry.get("moving_part")
        if category != "assembly_paths" or moving not in self.parts or moving in self.obstacles(entry):
            raise InputError("A boxes envelope is supported only for a loaded moving_part assembly path")
        envelope = _object(entry.get("envelope"), "path.envelope")
        _source(envelope.get("provenance"), "boxes envelope")
        specs = _list(envelope.get("boxes"), "envelope.boxes")
        if not specs or len(specs) > 5000:
            raise InputError("A boxes envelope requires between 1 and 5000 explicit boxes")
        translation = _vector(entry.get("translation_mm"), "path.translation_mm")
        if sum(value != 0 for value in translation) != 1 or max(abs(value) for value in translation) <= self.tol:
            raise InputError("Only nonzero box-axis-aligned rigid linear paths are supported")
        bounds = []
        for spec in specs:
            _, low, high = self.envelope(spec)
            bounds.append((low, high))
        coverage = self.kernel.boxes_contain_solids(self.parts[moving], bounds)
        self.add(f"{check_id}:containment", category,
                 "pass" if coverage["contained"] else "fail",
                 "independent_OCC_solid_bounds_contained_in_individual_declared_boxes",
                 {"moving_part": moving, **coverage,
                  "limitation": "Each actual solid must fit in at least one box; a union bounding box is not a containment proof."},
                 None if coverage["contained"] else "MOVING_PART_OUTSIDE_ENVELOPE",
                 None if coverage["contained"] else "At least one actual solid is not fully covered by an individual declared box.",
                 feature_id, repair="Add or enlarge explicit boxes to contain every real solid, including lower pins; do not omit moving geometry.")
        for index, ((low, high), spec) in enumerate(zip(bounds, specs)):
            swept_low = [min(a, a+t) for a, t in zip(low, translation)]
            swept_high = [max(b, b+t) for b, t in zip(high, translation)]
            swept = self.kernel.box(swept_low, swept_high)
            self.empty_envelope(f"{check_id}:box:{index}", category, feature_id, entry, swept,
                                {"sweep_method": "exact_continuous_union_of_individual_declared_box_translations",
                                 "box_index": index, "start_envelope": spec,
                                 "translation_mm": translation, "swept_box_min_mm": swept_low,
                                 "swept_box_max_mm": swept_high,
                                 "limitation": "Conservative explicit boxes; no rotation, flexibility, or nonlinear path proof."},
                                enforce_contact_policy=True)

    def finalize(self):
        required = [c for c in self.result["checks"] if c["required"]]
        self.result["check_summary"] = {
            status: sum(c["status"] == status for c in self.result["checks"])
            for status in ("pass", "fail", "not_checked", "not_applicable")}
        self.result["required_not_checked"] = [c["id"] for c in required if c["status"] == "not_checked"]
        if any(c["status"] == "fail" for c in required):
            self.result.update(status="failed", verdict="fail")
        elif any(c["status"] == "not_checked" for c in required) or not required:
            if self.result["status"] != "unsupported":
                self.result["status"] = "needs_input"
            self.result["verdict"] = "incomplete"
        else:
            self.result.update(status="ok", verdict="pass")
        return self.result


def verify(request_path: Path, cad_result_path: Path, output_dir: Path) -> Path:
    request_path, cad_result_path = request_path.resolve(), cad_result_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runner = Verifier(request_path, cad_result_path)
    try:
        runner.run()
    except Exception as exc:
        # Even malformed requests or unexpected OCC failures leave an auditable
        # structured failure rather than producing an apparent successful check.
        runner.add("verification_execution", "input", "not_checked", "execution_error",
                   {"reason": str(exc), "exception_type": type(exc).__name__},
                   "VERIFICATION_UNAVAILABLE", "Verification did not complete; no release approval.")
    result = runner.finalize()
    # Never overwrite an input artifact if the caller selected its directory.
    protected = {request_path, cad_result_path}
    protected.update(Path(g["path"]).resolve() for g in result["checked_geometry"])
    # Also protect referenced inputs that were rejected before registration.
    request = getattr(runner, "request", {})
    board = request.get("board", {})
    if isinstance(board, dict) and isinstance(board.get("step_path"), str):
        protected.add((request_path.parent / board["step_path"]).resolve())
    cad = getattr(runner, "cad", {})
    for artifact in cad.get("artifacts", []) if isinstance(cad.get("artifacts"), list) else []:
        if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
            protected.add((cad_result_path.parent / artifact["path"]).resolve())
    destination = output_dir / "verification.json"
    if destination.resolve() in protected:
        destination = output_dir / "verification-report.json"
    if destination.resolve() in protected:
        raise ValueError("Output paths overlap protected input artifacts")
    fd, temporary = tempfile.mkstemp(prefix=".verification-", suffix=".json", dir=output_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
