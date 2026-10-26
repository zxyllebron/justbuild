"""Optional model drivers. Providers propose JSON; CAD and checks remain deterministic."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def capabilities() -> dict:
    return {name: {"installed": bool(shutil.which(name)), "mode": "noninteractive CLI"} for name in ("codex", "claude")}


def propose(provider: str, prompt: str, schema: dict, workspace: Path, timeout: int = 180) -> dict:
    """Explicit opt-in CLI invocation; no shell interpolation or automatic command execution."""
    if provider not in ("codex", "claude") or not shutil.which(provider):
        raise ValueError("Requested model driver is not installed")
    with tempfile.TemporaryDirectory(prefix="justbuild-provider-") as directory:
        root = Path(directory)
        if provider == "codex":
            schema_path, result_path = root / "schema.json", root / "result.json"
            schema_path.write_text(json.dumps(schema))
            command = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                       "--output-schema", str(schema_path), "-o", str(result_path), prompt]
        else:
            command = ["claude", "-p", prompt, "--tools", "", "--output-format", "json", "--json-schema", json.dumps(schema)]
        result = subprocess.run(command, cwd=workspace, text=True, capture_output=True, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError(f"{provider} failed with exit code {result.returncode}; inspect private runner logs")
        if provider == "codex":
            return json.loads(result_path.read_text())
        payload = json.loads(result.stdout)
        value = payload.get("structured_output")
        if not isinstance(value, dict):
            raise ValueError("Claude did not return a structured object")
        return value

