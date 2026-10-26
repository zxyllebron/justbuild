"""User-dimension display envelope and explicitly provisional mechanical layout."""
from .request import need


def display_layout(pcb_bounds: dict, display: dict) -> dict:
    pl, ph = pcb_bounds["min_mm"], pcb_bounds["max_mm"]
    size = display["size_mm"]
    center = [(pl[i]+ph[i])/2 for i in (0, 1)]
    top = pl[2]-display["board_to_display_gap_mm"]
    lo = [center[0]-size[0]/2, center[1]-size[1]/2, top-size[2]]
    hi = [center[0]+size[0]/2, center[1]+size[1]/2, top]
    gap = display["clearance_xy_mm"]
    seating = {"min_mm": [lo[0]-gap, lo[1]-gap, lo[2]],
               "max_mm": [hi[0]+gap, hi[1]+gap, hi[2]]}
    return {"id": display["id"], "orientation": display["orientation"],
            "envelope_bounds": {"min_mm": lo, "max_mm": hi},
            "seating_region_bounds": seating, "size_mm": size,
            "geometry_role": "screen-envelope_user-dimensions",
            "source": display["source"], "manufacturer_component_model": False,
            "front_normal": [0, 0, -1], "active_area_verified": False,
            "ffc_bend_and_retention_verified": False,
            "pcb_post_bounds": [], "pcb_bridge_bounds": [], "edge_ledge_bounds": [],
            "face_opening_bounds": None, "ribbon_channel_bounds": None}
