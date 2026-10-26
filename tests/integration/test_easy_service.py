"""Review service discovery/startup only; no real model, printer or app launch."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import stat
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from justbuild import easy_service as easy

SECRET = "PRIVATE_ENV_MUST_NOT_BE_REPORTED"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUSTBUILD_EASY_DATA_DIR", raising=False)
    monkeypatch.setenv("PRIVATE_UNUSED_KEY", SECRET)
    calls = []
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    def spawn(argv, **kwargs):
        calls.append((argv, kwargs, stat.S_IMODE(os.fstat(kwargs["stdout"].fileno()).st_mode)))
        return process
    monkeypatch.setattr(easy.subprocess, "Popen", spawn)
    monkeypatch.setattr(easy, "_port_unused", lambda port: True)
    clock = [100.0]
    monkeypatch.setattr(easy, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000.0,
        sleep=lambda duration: clock.__setitem__(0, clock[0] + duration)))
    return SimpleNamespace(root=tmp_path, calls=calls, process=process, clock=clock,
                           state=tmp_path / "state/justbuild/easy", home=tmp_path / "home")


def test_online_compatible_service_uses_its_workspace_without_writes(rig, monkeypatch):
    active = rig.root / "already-online"
    ignored = rig.root / "ignored-file"; ignored.write_text("not a directory")
    monkeypatch.setattr(easy, "_workspace", lambda *args: str(active))
    result = easy.ensure_review_service(data_dir=ignored)
    assert result == {"base_url": "http://127.0.0.1:18765", "data_dir": str(active), "started": False}
    assert rig.calls == [] and not rig.state.exists() and not active.exists()


@pytest.mark.parametrize("mode", ["explicit", "environment", "default"])
def test_start_fixed_argv_private_log_and_inherited_configuration(rig, monkeypatch, mode):
    target = rig.root / "new workspace"
    if mode == "environment":
        monkeypatch.setenv("JUSTBUILD_EASY_DATA_DIR", str(target))
    if mode == "default":
        target = rig.home / ".local/share/justbuild/jobs"
    observed = Mock(side_effect=[None, None, None, str(target)])
    monkeypatch.setattr(easy, "_workspace", observed)
    monkeypatch.setenv("JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP", "0")
    monkeypatch.setenv("JUSTBUILD_ENABLE_LAN_PRINTING", "0")
    result = easy.ensure_review_service(port=18767, data_dir=target if mode == "explicit" else None)
    assert result["started"] is True and result["data_dir"] == str(target)
    assert result["base_url"] == "http://127.0.0.1:18767"
    assert len(rig.calls) == 1
    argv, kwargs, mode_bits = rig.calls[0]
    assert argv == [easy.sys.executable, "-m", "justbuild", "serve", "--host", "127.0.0.1", "--port", "18767", "--data-dir", str(target)]
    assert kwargs["stdin"] == easy.subprocess.DEVNULL and kwargs["stderr"] == easy.subprocess.STDOUT
    assert kwargs["start_new_session"] is kwargs["close_fds"] is True
    assert "shell" not in kwargs
    assert kwargs["env"]["PRIVATE_UNUSED_KEY"] == SECRET
    assert kwargs["env"]["JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP"] == kwargs["env"]["JUSTBUILD_ENABLE_LAN_PRINTING"] == "1"
    assert os.environ["JUSTBUILD_ENABLE_LAN_PRINTING"] == "0"  # Parent environment unchanged.
    assert mode_bits == 0o600
    assert stat.S_IMODE(Path(result["log_path"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(rig.state.stat().st_mode) == 0o700
    assert target.is_dir()
    record = json.loads((rig.state / "service-18767.json").read_text())
    assert record["pid"] == os.getpid() and record["phase"] == "spawned"
    assert SECRET not in json.dumps(result) and SECRET not in json.dumps(record)


def test_service_appearing_under_startup_lock_is_reused(rig, monkeypatch):
    target = str(rig.root / "raced-existing-service")
    monkeypatch.setattr(easy, "_workspace", Mock(side_effect=[None, target]))
    result = easy.ensure_review_service()
    assert result["started"] is False and result["data_dir"] == target and rig.calls == []


def test_refused_http_without_confirmed_unused_port_does_not_launch(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    monkeypatch.setattr(easy, "_port_unused", lambda port: False)
    with pytest.raises(ValueError, match="端口"):
        easy.ensure_review_service()
    assert rig.calls == []


def test_prior_alive_or_uncertain_launch_blocks_duplicate(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    state = easy._state_directory()
    record = state / "service-18765.json"
    for pid in (None, os.getpid()):
        record.write_text(json.dumps({"pid": pid, "phase": "starting"}))
        with pytest.raises(ValueError, match="不会重复启动"):
            easy.ensure_review_service()
    assert rig.calls == []


def test_timeout_preserves_process_and_never_relaunches(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    with pytest.raises(ValueError, match="时限"):
        easy.ensure_review_service(timeout=.25)
    assert len(rig.calls) == 1
    assert json.loads((rig.state / "service-18765.json").read_text())["pid"] == os.getpid()
    with pytest.raises(ValueError, match="不会重复启动"):
        easy.ensure_review_service(timeout=.25)
    assert len(rig.calls) == 1


def test_child_exits_once_and_is_not_restarted(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    rig.process.poll = lambda: 1
    with pytest.raises(ValueError, match="已退出"):
        easy.ensure_review_service()
    assert len(rig.calls) == 1


def test_failed_process_creation_error_does_not_echo_private_exception(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    def fail(*args, **kwargs):
        raise OSError(SECRET)
    monkeypatch.setattr(easy.subprocess, "Popen", fail)
    with pytest.raises(ValueError) as error:
        easy.ensure_review_service()
    assert "未能启动" in str(error.value) and SECRET not in str(error.value)
    assert not (rig.state / "service-18765.json").exists()


@pytest.mark.parametrize("port,timeout", [(0, 12), (65536, 12), (True, 12), ("18765", 12),
    (18765, 0), (18765, -1), (18765, True), (18765, float("nan")), (18765, float("inf"))])
def test_invalid_arguments_fail_before_connection_or_files(rig, monkeypatch, port, timeout):
    observed = Mock(); monkeypatch.setattr(easy, "_workspace", observed)
    with pytest.raises(ValueError):
        easy.ensure_review_service(port=port, timeout=timeout)
    observed.assert_not_called()
    assert not rig.state.exists() and rig.calls == []


@pytest.mark.parametrize("status,payload", [
    (200, {"product": "justbuild", "interface": "easy-v1", "data_dir": "/tmp/review-root"}),
    (302, {"message": "redirect"}),
    (404, {"detail": "old service"}),
    (200, {"product": "other", "interface": "easy-v1", "data_dir": "/tmp/review-root"}),
    (200, {"product": "justbuild", "interface": "old", "data_dir": "/tmp/review-root"}),
    (200, {"product": "justbuild", "interface": "easy-v1", "data_dir": "relative"}),
    (200, {"product": "justbuild", "interface": "easy-v1", "data_dir": None}),
    (200, ["wrong shape"]),
    (200, "not json"),
    (200, {"padding": "x" * 17000}),
])
def test_isolated_http_contract_no_proxy_no_redirect_no_service_replacement(rig, monkeypatch, status, payload):
    requests = []
    body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    # Use a real clock only for the isolated direct HTTP request/timer.
    import time
    monkeypatch.setattr(easy, "time", time)
    try:
        if status == 200 and isinstance(payload, dict) and payload.get("data_dir") == "/tmp/review-root" and payload.get("product") == "justbuild" and payload.get("interface") == "easy-v1":
            result = easy.ensure_review_service(port=server.server_port)
            assert result["data_dir"] == "/tmp/review-root" and result["started"] is False
        else:
            with pytest.raises(ValueError) as error:
                easy.ensure_review_service(port=server.server_port)
            assert SECRET not in str(error.value)
        assert requests == ["/api/local/workspace"]
        assert rig.calls == [] and not rig.state.exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_invalid_data_path_is_chinese_and_does_not_expose_input(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    with pytest.raises(ValueError) as error:
        easy.ensure_review_service(data_dir="bad\0" + SECRET)
    assert "任务目录" in str(error.value) and SECRET not in str(error.value)
    assert rig.calls == []


def test_symlink_state_is_rejected_without_start(rig, monkeypatch):
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    real_state = rig.root / "real-state"; real_state.mkdir()
    (rig.root / "state").symlink_to(real_state, target_is_directory=True)
    with pytest.raises(ValueError, match="私有启动目录"):
        easy.ensure_review_service()
    assert rig.calls == []


def test_lock_wait_uses_same_total_deadline_and_never_starts_second_process(rig, monkeypatch):
    import fcntl
    monkeypatch.setattr(easy, "_workspace", lambda *args: None)
    def busy(*args, **kwargs):
        raise BlockingIOError()
    monkeypatch.setattr(fcntl, "flock", busy)
    with pytest.raises(ValueError, match="时限"):
        easy.ensure_review_service(timeout=.12)
    assert rig.clock[0] >= 100.12 and rig.calls == []


def test_non_refused_connection_problem_never_triggers_start(rig, monkeypatch):
    class TimeoutConnection:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            raise socket.timeout(SECRET)
        def close(self):
            pass
    monkeypatch.setattr(easy.http.client, "HTTPConnection", TimeoutConnection)
    with pytest.raises(ValueError) as error:
        easy.ensure_review_service()
    assert SECRET not in str(error.value)
    assert rig.calls == [] and not rig.state.exists()


def test_two_simultaneous_wizards_share_one_background_start(rig, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    monkeypatch.setattr(easy, "time", time)
    first_probe = threading.Barrier(2)
    seen = set(); guard = threading.Lock(); ready = threading.Event()
    target = rig.root / "concurrent-jobs"
    def workspace(*args):
        identity = threading.get_ident()
        with guard:
            first = identity not in seen
            seen.add(identity)
        if first:
            first_probe.wait(timeout=2)
            return None
        return str(target) if ready.is_set() else None
    monkeypatch.setattr(easy, "_workspace", workspace)
    def spawn(argv, **kwargs):
        with guard:
            rig.calls.append((argv, kwargs, 0o600))
        ready.set()
        return rig.process
    monkeypatch.setattr(easy.subprocess, "Popen", spawn)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: easy.ensure_review_service(data_dir=target, timeout=3), range(2)))
    assert len(rig.calls) == 1
    assert sorted(result["started"] for result in results) == [False, True]
    assert all(result["data_dir"] == str(target) for result in results)
