from copy import deepcopy

import pytest

from justbuild.review import validate_review


def fixture():
    return {"constraints": {"feature_review": {"mounting_holes_complete": True,
            "ports_complete": True, "keepouts_complete": True, "source": "test explicit scopes"},
            "ports": [{"id": "access-solid-1", "solid_index": 1, "access": "internal",
                       "component_kind": "unknown", "semantic_review_status": "pending"}]}}


def test_total_confirmation_does_not_classify_an_internal_button():
    original = fixture()
    with pytest.raises(ValueError, match="unreviewed function"):
        validate_review(deepcopy(original), original)


def test_explicit_component_classification_is_accepted():
    original = fixture()
    reviewed = deepcopy(original)
    reviewed["constraints"]["ports"][0].update(component_kind="button", access="maintenance",
        semantic_review_status="reviewed", semantic_review_source="identified button from named STEP and user review")
    validate_review(reviewed, original)


def test_omitting_unrecognized_component_is_not_a_review():
    original = fixture()
    reviewed = deepcopy(original)
    reviewed["constraints"]["ports"] = []
    with pytest.raises(ValueError, match="unreviewed function"):
        validate_review(reviewed, original)


def test_reviewed_named_group_can_replace_separate_solid_candidates():
    original = fixture()
    reviewed = deepcopy(original)
    reviewed["constraints"]["ports"] = [{"id": "button-access", "access": "maintenance"}]
    reviewed["constraints"]["functional_features"] = [{"kind": "button", "reviewed": True,
        "source": "named STEP component", "solid_indices": [1], "port_ids": ["button-access"]}]
    validate_review(reviewed, original)
    reviewed["constraints"]["functional_features"][0]["port_ids"] = ["missing-access"]
    with pytest.raises(ValueError, match="unreviewed function"):
        validate_review(reviewed, original)


def test_incomplete_scope_is_rejected():
    original = fixture()
    reviewed = deepcopy(original)
    reviewed["constraints"]["feature_review"]["ports_complete"] = False
    with pytest.raises(ValueError, match="each require"):
        validate_review(reviewed, original)


def test_static_access_never_claims_physical_operation():
    from justbuild.integration import functional_access_checks
    request = {"constraints": {"ports": [{"id": "key-window"}], "functional_features": [
        {"id": "KEY", "reviewed": True, "source": "named STEP", "solid_indices": [1],
         "port_ids": ["key-window"]}]}}
    checks = functional_access_checks(request)
    assert checks[0]["status"] == "pass"
    assert all(check["status"] == "not_checked" for check in checks[1:])
    request["constraints"]["ports"] = []
    checks = functional_access_checks(request)
    assert checks[0]["status"] == "fail"
    assert checks[0]["required"] is True
