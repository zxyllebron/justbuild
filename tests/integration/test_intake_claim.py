"""Validate one-time intake handoff behavior without network or browser access."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_intake_claim_clears_fragment_before_single_redacted_request():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the intake claim module checks")
    completed = subprocess.run(
        [node, str(Path(__file__).with_suffix(".mjs"))], check=True,
        capture_output=True, text=True, timeout=30,
    )
    result = json.loads(completed.stdout)
    assert result["passed"] is True
    assert result["tests"] == 7
