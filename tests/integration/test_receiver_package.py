"""Receiver package checks, plus Windows-only real PowerShell download checks.

Linux checks do not establish Windows execution or printer acceptance.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from contextlib import contextmanager

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "windows-receiver.ps1"
DOC = ROOT / "docs" / "windows-receiver.md"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def test_receiver_is_packaged_with_explicit_evidence_limits():
    script = SCRIPT.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    assert "#requires -Version 5.1" in script
    assert "printer_receipt = 'not_observed'" in script
    assert "print_start = 'not_observed'" in script
    assert "Windows 实机验证" in doc


def test_receiver_has_no_remote_command_or_protocol_installation():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "Invoke-Expression" not in script
    assert "Start-Process" not in script
    assert "Register-ScheduledTask" not in script
    assert "$start.FileName = $studio" in script
    assert "$start.UseShellExecute = $false" in script
    assert "$handler.AllowAutoRedirect = $false" in script
    assert "[IO.FileShare]::None" in script
    assert script.index("$receipt.open_status = 'launch_unknown'") < script.index("[Diagnostics.Process]::Start($start)")


@contextmanager
def handoff_server(*, bad_hash=False, redirect=False, cross_origin=False):
    payload = b"receiver-test-3mf-download-only"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/handoffs/token/manifest":
                manifest = {
                    "id": "test-handoff",
                    "download_url": "https://different-origin.invalid/file.3mf" if cross_origin else "/file.3mf",
                    "sha256": "0" * 64 if bad_hash else hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    "basename": "test.gcode.3mf",
                }
                body = json.dumps(manifest).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/file.3mf":
                if redirect:
                    self.send_response(302)
                    self.send_header("Location", "/redirected.3mf")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/handoffs/token", payload
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def receive(url, local_app_data, *, download_only=True):
    env = dict(os.environ, LOCALAPPDATA=str(local_app_data))
    return subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), "-HandoffUrl", url,
         "-AllowLoopbackHttp", *(["-DownloadOnly"] if download_only else [])],
        env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.skipif(SHELL is None, reason="Requires Windows PowerShell; Linux package checks are not execution evidence")
def test_windows_receiver_download_and_repeat(tmp_path):
    with handoff_server() as (url, payload):
        first = receive(url, tmp_path)
        assert first.returncode == 0, first.stderr
        receipt = json.loads(first.stdout)
        assert receipt["download_status"] == "verified"
        assert receipt["open_status"] == "pending"
        assert receipt["print_start"] == "not_observed"
        assert Path(receipt["local_path"]).read_bytes() == payload
        second = receive(url, tmp_path)
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["local_path"] == receipt["local_path"]


@pytest.mark.skipif(SHELL is None, reason="Requires Windows PowerShell")
@pytest.mark.parametrize("open_status", ["launch_unknown", "process_started"])
def test_windows_receiver_never_relaunches_recorded_attempt(tmp_path, open_status):
    with handoff_server() as (url, _payload):
        first = receive(url, tmp_path)
        assert first.returncode == 0, first.stderr
        receipt = json.loads(first.stdout)
        receipt["open_status"] = open_status
        receipt_path = tmp_path / "justbuild" / "receipts" / "test-handoff.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        # Without -DownloadOnly this would attempt app discovery/launch if dedup failed.
        again = receive(url, tmp_path, download_only=False)
        assert again.returncode == 0, again.stderr
        assert json.loads(again.stdout)["open_status"] == open_status


@pytest.mark.skipif(SHELL is None, reason="Requires Windows PowerShell")
@pytest.mark.parametrize("failure", ["bad_hash", "redirect", "cross_origin"])
def test_windows_receiver_rejects_invalid_handoffs(tmp_path, failure):
    with handoff_server(**{failure: True}) as (url, _payload):
        result = receive(url, tmp_path)
        assert result.returncode != 0
        assert not list(tmp_path.rglob("*.3mf"))
        assert not list(tmp_path.rglob("*.partial"))
