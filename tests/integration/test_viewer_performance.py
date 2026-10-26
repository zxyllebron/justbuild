"""Run real JavaScript geometry/viewer contracts without a browser or native GUI."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_software_viewer_geometry_cache_and_render_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser-module contract checks")
    script = Path(__file__).with_suffix(".mjs")
    completed = subprocess.run([node, str(script)], check=True, capture_output=True, text=True, timeout=45)
    result = json.loads(completed.stdout)
    assert result["passed"] is True
    assert result["tests"] >= 9
