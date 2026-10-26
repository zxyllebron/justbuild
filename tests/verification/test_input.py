"""These contract/error tests run even when the geometry backend is absent."""

import json

import pytest

from justbuild.verification import verify


@pytest.mark.parametrize("body", ["{", '{"job_id":"x","job_id":"y"}',
                                  '{"job_id":"x","bad":NaN}',
                                  '{"job_id":"x","bad":1e999}', "[]"])
def test_malformed_input_produces_structured_incomplete_report(tmp_path, body):
    request = tmp_path / "request.json"
    cad = tmp_path / "cad.json"
    request.write_text(body)
    cad.write_text("{}")
    path = verify(request, cad, tmp_path / "out")
    report = json.loads(path.read_text())
    assert report["verdict"] == "incomplete"
    assert report["status"] == "needs_input"
    assert report["issues"][0]["code"] == "VERIFICATION_UNAVAILABLE"


def test_output_does_not_overwrite_input(tmp_path):
    request = tmp_path / "verification.json"
    cad = tmp_path / "cad.json"
    request.write_text("{}")
    cad.write_text("{}")
    path = verify(request, cad, tmp_path)
    assert path != request
    assert request.read_text() == "{}"
