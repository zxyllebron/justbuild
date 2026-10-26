"""Synthetic BRep evidence for screen packing; no device access."""
import hashlib
import json
from pathlib import Path

import pytest

from justbuild.verification.display import display_checks


@pytest.fixture
def screen_case(tmp_path):
    cq = pytest.importorskip("cadquery")
    from justbuild.verification.geometry import box, cylinder

    class ScreenCase:
        def __init__(self):
            self.root = tmp_path
            self.request = {"job_id": "screen-verification-fixture", "board": {"units": "mm", "metadata": {"pcb_solid_index": 0}},
                "constraints": {"confirmed": True,
                    "board_frame": {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
                    "display": {"id": "screen", "size_mm": [110, 70, 3.2], "orientation": "pcb_back_parallel",
                        "clearance_xy_mm": .5, "clearance_z_mm": .5, "board_to_display_gap_mm": 4,
                        "pcb_post_height_mm": 2, "pcb_bridge_thickness_mm": 1.5,
                        "face_opening": {"mode": "prototype_edge_ledge", "ledge_width_mm": 1},
                        "ribbon_channel": {"face": "x_max", "center_tangent_mm": 30, "width_mm": 40,
                                           "height_mm": 2, "source": "synthetic prototype routing requirement"}},
                    "manufacturing": {"wall_thickness_mm": 2, "standoff_height_mm": 2,
                        "geometry_tolerance_mm": .001, "boolean_volume_tolerance_mm3": 1e-7,
                        "pcb_mount": {"post_diameter_mm": 3.8, "pilot_diameter_mm": 1.3}},
                    "mounting_holes": [{"id": f"p{i}", "center_mm": [x, y, 0]} for i, (x, y) in enumerate(((2, 2), (98, 2), (2, 58), (98, 58)))],
                }}
            self.board = box([0, 0, 0], [100, 60, 1.6])
            self.screen = box([-5, -5, -7.2], [105, 65, -4])
            self.base = box([-7.5, -7.5, -9.7], [107.5, 67.5, 12]).cut(box([-5.5, -5.5, -7.7], [105.5, 65.5, 13]))
            self.base = self.base.cut(box([-4, -4, -10], [104, 64, -7.2]))
            self.base = self.base.fuse(box([-5.5, -5.5, -7.7], [105.5, 65.5, -7.2]).cut(box([-4, -4, -8], [104, 64, -7])))
            self.base = self.base.cut(box([98, 10, -3.5], [108, 50, -1.5]))
            for h in self.request["constraints"]["mounting_holes"]:
                x, y, _ = h["center_mm"]
                a, b = (-5.5, y + 1.9) if y < 30 else (y - 1.9, 65.5)
                self.base = self.base.fuse(box([x - 1.9, a, -3.5], [x + 1.9, b, -2]))
                self.base = self.base.fuse(cylinder([x, y, -2], [0, 0, 1], 3.8, [0, 2]))
                self.base = self.base.cut(cylinder([x, y, -1.5], [0, 0, 1], 1.3, [0, 2]))
            self.lid = box([-7.5, -7.5, 12.1], [107.5, 67.5, 14.1])
            self.carrier = None

        def run(self, mutate_manifest=None):
            artifacts = []
            parts = [("original_board", self.board), ("base", self.base), ("lid", self.lid), ("screen_envelope", self.screen)]
            if self.carrier is not None:
                parts.append(("pcb_carrier", self.carrier))
            for part, shape in parts:
                path = self.root / (part + ".step")
                cq.exporters.export(shape, str(path))
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if part == "original_board":
                    self.request["board"].update(step_path=str(path), sha256=sha)
                else:
                    artifacts.append({"kind": part + "_step", "path": path.name, "sha256": sha, "units": "mm"})
            cad = {"job_id": self.request["job_id"], "status": "ok", "artifacts": artifacts}
            if mutate_manifest:
                mutate_manifest(cad)
            path = self.root / "cad.json"
            path.write_text(json.dumps(cad))
            return {c["id"]: c for c in display_checks(self.request, cad, path)}

    return ScreenCase()


def test_screen_brep_checks_and_explicit_unverified_inputs(screen_case):
    checks = screen_case.run()
    assert checks["display_envelope_pose"]["status"] == "pass"
    for name in ("display_no_penetration", "display_component_clearance", "display_seating_clearance", "display_front_opening", "display_edge_ledge", "display_ribbon_static_channel"):
        assert checks[name]["status"] == "pass", checks
    assert checks["display_active_area"]["required"] is True
    assert checks["display_active_area"]["status"] == "not_checked"
    assert checks["display_ribbon_bend"]["status"] == "not_checked"
    assert checks["display_screen_assembly"]["status"] == "not_checked"
    assert checks["display_screen_assembly"]["measurements"]["supported_axial_probes_both_blocked"] is True
    assert all(c["status"] == "pass" for name, c in checks.items() if name.startswith("display_pcb_post:"))


def test_post_penetrating_screen_is_not_excused(screen_case):
    from justbuild.verification.geometry import box
    screen_case.base = screen_case.base.fuse(box([1, -5.5, -7.3], [3, 3, 0]))
    checks = screen_case.run()
    assert checks["display_no_penetration"]["status"] == "fail"
    assert checks["display_seating_clearance"]["status"] == "fail"


def test_screen_gap_is_real_brep_not_metadata(screen_case):
    from justbuild.verification.geometry import box
    screen_case.base = screen_case.base.fuse(box([104.6, 0, -7.2], [105.5, 20, -4]))
    checks = screen_case.run()
    assert checks["display_seating_clearance"]["status"] == "fail"


def test_same_bbox_hollow_screen_does_not_match_solid_envelope(screen_case):
    from justbuild.verification.geometry import box
    screen_case.screen = screen_case.screen.cut(box([0, 0, -7], [100, 60, -4.1]))
    checks = screen_case.run()
    assert checks["display_envelope_pose"]["status"] == "fail"


def test_original_bottom_component_collision_is_checked(screen_case):
    import cadquery as cq
    from justbuild.verification.geometry import box
    screen_case.board = cq.Compound.makeCompound([screen_case.board, box([45, 25, -5], [55, 35, 0])])
    checks = screen_case.run()
    assert checks["display_no_penetration"]["status"] == "fail"
    assert checks["display_component_clearance"]["status"] == "fail"


def test_ribbon_blockage_fails(screen_case):
    from justbuild.verification.geometry import box
    screen_case.base = screen_case.base.fuse(box([105.5, 29, -3.5], [107.5, 31, -1.5]))
    checks = screen_case.run()
    assert checks["display_ribbon_static_channel"]["status"] == "fail"


def test_front_opening_blockage_fails(screen_case):
    from justbuild.verification.geometry import box
    screen_case.base = screen_case.base.fuse(box([-5.5, 29, -9.7], [105.5, 31, -7.7]))
    checks = screen_case.run()
    assert checks["display_front_opening"]["status"] == "fail"


def test_missing_ledge_cannot_pass_as_empty_floor(screen_case):
    from justbuild.verification.geometry import box
    screen_case.base = screen_case.base.cut(box([-5.2, -5.2, -10], [105.2, 65.2, -7]))
    checks = screen_case.run()
    assert checks["display_edge_ledge"]["status"] == "fail"


def test_invalid_dimensions_fail_closed(screen_case):
    screen_case.request["constraints"]["display"]["size_mm"][2] = float("nan")
    checks = screen_case.run()
    assert checks["display_definition"]["status"] == "not_checked"


def test_hash_tamper_fails_closed(screen_case):
    def tamper(cad):
        cad["artifacts"][0]["sha256"] = "0" * 64
    checks = screen_case.run(tamper)
    assert checks["display_geometry_evidence"]["status"] == "not_checked"
    assert "hash mismatch" in checks["display_geometry_evidence"]["measurements"]["reason"]


def test_historical_request_does_not_acquire_display_checks():
    assert display_checks({"constraints": {}}, {}, Path("absent.json")) == []


def test_removable_carrier_bears_original_pcb_posts(screen_case):
    from justbuild.verification.geometry import box
    region = box([-5.5, -5.5, -3.5], [105.5, 65.5, .01])
    screen_case.carrier = screen_case.base.intersect(region)
    screen_case.base = screen_case.base.cut(region)
    screen_case.request["constraints"]["display"]["support_structure"] = "removable_pcb_carrier"
    checks = screen_case.run()
    for name, check in checks.items():
        if name.startswith("display_pcb_post:"):
            assert check["status"] == "pass"
            assert check["measurements"]["support_part"] == "pcb_carrier"
    assert checks["display_no_penetration"]["status"] == "pass"
    assert any(row["part_id"] == "pcb_carrier" for row in checks["display_ribbon_static_channel"]["measurements"]["obstacles"])
    # The core verifier requires and checks both screen and carrier assembly
    # paths for this explicit structure; the supplement does not invent a pass.
    assert "display_screen_assembly" not in checks


@pytest.mark.parametrize("clamp_label", ["shared_lid_m3", "shared_lid_fastener"])
def test_m2_sizes_thin_shell_and_both_clamp_labels_remain_compatible(screen_case, clamp_label):
    """Compatibility only: this does not claim independent M2 bore/head checks."""
    from justbuild.verification.geometry import box
    c = screen_case.request["constraints"]
    c["display"].update(support_structure="removable_pcb_carrier", carrier_clamp=clamp_label)
    m = c["manufacturing"]
    m.update(wall_thickness_mm=1.6, base_thickness_mm=1.6, lid_thickness_mm=2.0)
    m["lid_fastener"] = {"post_diameter_mm": 6.2, "pilot_diameter_mm": 1.6,
        "pilot_depth_mm": 4.5, "clearance_diameter_mm": 2.2, "head_diameter_mm": 4.0,
        "head_depth_mm": 1.2, "screw_length_mm": 14.0}
    # Preserve original PCB positions, short posts and screen geometry while
    # genuinely reducing the fixture's enclosing wall/floor and lid extent.
    screen_case.base = screen_case.base.intersect(box([-7.1, -7.1, -9.3], [107.1, 67.1, 12]))
    support_region = box([-5.5, -5.5, -3.5], [105.5, 65.5, .01])
    screen_case.carrier = screen_case.base.intersect(support_region)
    screen_case.base = screen_case.base.cut(support_region)
    screen_case.lid = box([-7.1, -7.1, 12.1], [107.1, 67.1, 14.1])
    checks = screen_case.run()
    for identity in ("display_dimension_contract", "display_envelope_pose", "display_no_penetration",
                     "display_seating_clearance", "display_edge_ledge", "display_ribbon_static_channel"):
        assert checks[identity]["status"] == "pass", checks
    assert checks["display_ribbon_static_channel"]["measurements"]["min_mm"][0] == pytest.approx(98.4)
    assert all(check["status"] == "pass" for name, check in checks.items() if name.startswith("display_pcb_post:"))
    assert checks["display_active_area"]["status"] == "not_checked"
    assert checks["display_ribbon_bend"]["status"] == "not_checked"
