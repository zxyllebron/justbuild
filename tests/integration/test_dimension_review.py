"""Execute dimensional provenance and SVG projection behavior in the real JS module."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_dimension_review_provenance_and_projection():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser dimension-module checks")
    completed = subprocess.run(
        [node, str(Path(__file__).with_suffix(".mjs"))],
        check=True, capture_output=True, text=True, timeout=30,
    )
    result = json.loads(completed.stdout)
    assert result["passed"] is True
    assert result["tests"] >= 10
