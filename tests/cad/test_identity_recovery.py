"""Recovery preserves original source bytes and never edits a failed result."""
import hashlib
import json

import pytest

from justbuild.cad import generate
from justbuild.cad.recovery import native_mm_units, resume_identity_source_preservation
from justbuild.cad.request import CadError
from synthetic_fixture import make_fixture


@pytest.fixture
def checkpoint(tmp_path):
    request = make_fixture(tmp_path)
    result_path = generate(request, tmp_path / "runs")
    result = json.loads(result_path.read_text())
    assert result["status"] == "ok", result["issues"]
    # Fault-injected optional reencoding failure; required case checks remain
    # genuine checks from this fixture's successful generation.
    result.update(status="failed", artifacts=[], issues=[{"code": "GEOMETRY_CHECK_FAILED"}])
    drift = next(c for c in result["checks"] if c["name"] == "normalized_input_step_roundtrip")
    drift.update(status="fail", measurements={"volume_delta_mm3": .5})
    result_path.write_text(json.dumps(result))
    return request, result_path


def test_fresh_result_reuses_byte_identical_mm_source_and_documents_rejected_export(checkpoint):
    request, old = checkpoint
    before = old.read_bytes()
    path = resume_identity_source_preservation(request, old, old.parent.parent)
    result = json.loads(path.read_text())
    assert old.read_bytes() == before
    assert path.parent != old.parent and result["status"] == "ok"
    artifacts = {a["kind"]: a for a in result["artifacts"]}
    assert "assembly_step" not in artifacts and "assembly_manifest" in artifacts
    source_sha = result["metadata"]["input_provenance"]["step_sha256"]
    assert artifacts["source_step"]["sha256"] == artifacts["normalized_input_step"]["sha256"] == source_sha
    assert (path.parent / artifacts["source_step"]["path"]).read_bytes() == (path.parent / artifacts["normalized_input_step"]["path"]).read_bytes()
    assert all(hashlib.sha256((path.parent / a["path"]).read_bytes()).hexdigest() == a["sha256"]
               for a in artifacts.values())
    assert any(c["name"] == "normalized_input_byte_preservation" and c["status"] == "pass"
               for c in result["checks"])
    assert not any(c["status"] == "fail" for c in result["checks"])
    assert result["metadata"]["recovery"]["independent_verification_required"] is True
    assert any(i["code"] == "OPTIONAL_SOURCE_REENCODING_REJECTED" for i in result["issues"])


@pytest.mark.parametrize("change", ["request", "source", "frame", "other_failure", "missing_case_check", "changed_case"])
def test_unrelated_or_changed_inputs_cannot_reuse_prior_checks(checkpoint, change):
    request, old = checkpoint
    if change in ("request", "frame"):
        value = json.loads(request.read_text())
        if change == "frame":
            value["constraints"]["board_frame"]["origin_mm"] = [1, 0, 0]
        else:
            value["constraints"]["manufacturing"]["source"] += " revised"
        request.write_text(json.dumps(value))
    elif change == "source":
        value = json.loads(request.read_text())
        source = request.parent / value["board"]["step_path"]
        source.write_bytes(source.read_bytes() + b"\n")
    elif change in ("other_failure", "missing_case_check"):
        value = json.loads(old.read_text())
        target = next(c for c in value["checks"] if c["name"] == "base_input_nonpenetration")
        target["status"] = "fail" if change == "other_failure" else "not_checked"
        old.write_text(json.dumps(value))
    else:
        import cadquery as cq
        cq.exporters.export(cq.Solid.makeBox(1, 1, 1), str(old.parent / "base.step"))
    with pytest.raises(CadError):
        resume_identity_source_preservation(request, old, old.parent.parent)


def test_non_mm_source_cannot_be_called_byte_preserved_normalized_mm(checkpoint):
    _, old = checkpoint
    source = old.parent / "source.step"
    source.write_text(source.read_text().replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)"))
    with pytest.raises(CadError, match="native millimeter"):
        native_mm_units(source)
