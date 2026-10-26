"""Synthetic mechanical reference only; no dimensions derived from the V821 image."""
from __future__ import annotations

import tempfile
from pathlib import Path

from .contracts import write_json
from .inspection import default_manufacturing


def create_demo(manager, wait: bool = False) -> dict:
    import cadquery as cq

    with tempfile.TemporaryDirectory(prefix="justbuild-reference-") as directory:
        folder = Path(directory)
        # Board is XY-aligned, 80 x 55 x 1.6 mm, four real through-holes in synthetic STEP.
        holes = [(5, 5), (75, 5), (5, 50), (75, 50)]
        pcb = cq.Workplane("XY").box(80, 55, 1.6, centered=(False, False, False))
        for x, y in holes:
            pcb = pcb.cut(cq.Workplane("XY").center(x, y).circle(1.5).extrude(1.6))
        usb = cq.Workplane("XY").box(14, 10, 5, centered=(False, False, False)).translate((-2, 17, 1.6))
        chip = cq.Workplane("XY").box(18, 18, 3, centered=(False, False, False)).translate((30, 20, 1.6))
        header = cq.Workplane("XY").box(4, 18, 8, centered=(False, False, False)).translate((64, 22, 1.6))
        shape = cq.Compound.makeCompound([pcb.val(), usb.val(), chip.val(), header.val()])
        source = folder / "synthetic-board.step"
        cq.exporters.export(shape, str(source))
        # Preserve this versioned offline calibration sample. New real-board
        # proposals use inspection.default_manufacturing's compact fit rules.
        reference_manufacturing = default_manufacturing()
        reference_manufacturing.pop("enclosure_fit", None)
        reference_manufacturing.pop("fit_policy", None)
        reference_manufacturing.update(clearance_xy_mm=1.0, clearance_top_mm=2.0)
        request = {"schema_version": "0.1", "job_id": "synthetic-reference",
                   "board": {"step_path": str(source), "units": "mm", "metadata": {
                       "pcb_solid_index": 0, "pcb_selection_source": "synthetic reference fixture construction"}},
                   "constraints": {"confirmed": False, "structure": "single_pcb_screw_lid",
                       "board_frame": {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
                       "feature_review": {"mounting_holes_complete": True, "ports_complete": True, "keepouts_complete": True,
                                          "source": "explicit synthetic fixture; not a real-board approval"},
                       "mounting_holes": [{"id": f"mount-{i+1}", "reference": "pcb", "center_mm": [x, y, .8],
                                           "axis": [0, 0, 1], "diameter_mm": 3.0, "source": "synthetic STEP construction"}
                                          for i, (x, y) in enumerate(holes)],
                       "ports": [{"id": "usb-demo", "label": "合成 USB 接口", "access": "external", "shape": "rectangle",
                                  "face": "x_min", "center_mm": [-2, 22, 4.1], "width_mm": 12, "height_mm": 7,
                                  "insertion_direction": [1, 0, 0], "source": "synthetic USB and plug geometry",
                                  "service_envelope": {"min_mm": [-30, 16, .6], "max_mm": [-2, 28, 7.6],
                                                       "source": "synthetic rectangular plug fixture"}},
                                 {"id": "header-demo", "label": "预留排针（本例不接线）", "access": "covered", "source": "synthetic unused header; no connected cable in this fixture"}],
                       "keepouts": [], "manufacturing": reference_manufacturing}}
        job = manager.create(source, request, "合成参考板 · 80 × 55 mm", synthetic=True)
        if job["state"] != "awaiting_confirmation":
            return job
        confirmed = job["proposal"]["request"]
        confirmed["constraints"]["confirmed"] = True
        settings = manager.slicing.synthetic_settings(manager.directory(job["job_id"]))
        if settings:
            confirmed["printing"] = settings
        manager.update(job["job_id"], proposal={**job["proposal"],"request":confirmed})
        return manager.confirm(job["job_id"], confirmed, wait=wait)

