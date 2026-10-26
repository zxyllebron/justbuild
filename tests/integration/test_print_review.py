"""Check the real print-review browser module against mock API and minimal DOM."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_print_review_explicit_action_and_unknown_outcome_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for print-review UI contracts")
    completed = subprocess.run(
        [node, str(Path(__file__).with_suffix(".mjs"))],
        check=True, capture_output=True, text=True, timeout=30,
    )
    result = json.loads(completed.stdout)
    assert result["passed"] is True
    assert result["tests"] >= 16
