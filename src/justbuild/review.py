"""A total checkbox cannot silently classify unrecognized STEP components."""
from __future__ import annotations


COMPONENT_KINDS = frozenset({"connector", "button", "switch", "card_slot", "ribbon",
                             "antenna", "indicator", "internal_component"})


def validate_review(request: dict, original: dict) -> None:
    constraints = request.get("constraints", {})
    review = constraints.get("feature_review", {})
    if not all(review.get(name + "_complete") is True for name in ("mounting_holes", "ports", "keepouts")):
        raise ValueError("Mounting holes, functional access and keepouts each require an explicit complete review")
    if not isinstance(review.get("source"), str) or not review["source"].strip():
        raise ValueError("Feature review provenance is required")
    ports = {p.get("id"): p for p in constraints.get("ports", []) if isinstance(p, dict)}
    features = constraints.get("functional_features", [])
    for old in original.get("constraints", {}).get("ports", []):
        if old.get("semantic_review_status") != "pending":
            continue
        candidate = ports.get(old.get("id"), {})
        if (candidate.get("semantic_review_status") == "reviewed"
                and candidate.get("component_kind") in COMPONENT_KINDS
                and candidate.get("solid_index") == old.get("solid_index")
                and candidate.get("semantic_review_source")):
            continue
        # A named assembly component can legitimately group many STEP solids.
        grouped = [f for f in features if isinstance(f, dict)
                   and old.get("solid_index") in f.get("solid_indices", [])
                   and f.get("reviewed") is True and f.get("source")
                   and isinstance(f.get("kind"), str) and f["kind"].strip() not in ("", "unknown")
                   and f.get("port_ids") and all(p in ports for p in f["port_ids"])]
        if not grouped:
            raise ValueError(f"Component {old.get('solid_index')} still has an unreviewed function; classify it or link it to a reviewed functional feature")
