"""Mock HTTP and mock process tests; never launch Studio or a printer."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from justbuild import receiver
from justbuild.receiver import ReceiverError, receive


@pytest.fixture
def sample(tmp_path, monkeypatch):
    token = "c" * 64
    origin = "https://trusted.example"
    url = origin + "/handoffs/" + token
    data = b"PK\x03\x04" + bytes(range(256)) * 3
    manifest = {"id": "handoff-proof", "download_url": url + "/file.gcode.3mf",
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "basename": "enclosure.gcode.3mf"}
    calls, launched = [], []
    state = {"manifest": manifest, "manifest_status": 200, "file_status": 200, "raw_manifest": None}

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/manifest"):
            body = state["raw_manifest"] or json.dumps(state["manifest"]).encode()
            return httpx.Response(state["manifest_status"], content=body,
                                  headers={"Location": "https://untrusted.example/leak"})
        return httpx.Response(state["file_status"], content=data,
                              headers={"Location": "https://untrusted.example/leak"})

    def unexpected_launch(*args, **kwargs):
        launched.append((args, kwargs))
        pytest.fail("Studio must not start in download-only tests")

    monkeypatch.setattr(receiver.subprocess, "Popen", unexpected_launch)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    root = tmp_path / "cache"
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/bin/sh\nexit 99\n")
    launcher.chmod(0o700)
    t = SimpleNamespace(token=token, url=url, origin=origin, data=data, state=state,
                        client=client, root=root, launcher=launcher, calls=calls, launched=launched)
    yield t
    client.close()


def run(t, **options):
    return receive(t.url, allowed_origin=t.origin, cache_root=t.root, client=t.client, **options)


def test_default_exact_bytes_hash_receipt_and_download_deduplication(sample):
    t = sample
    first = run(t)
    destination = Path(first["local_path"])
    assert destination.read_bytes() == t.data
    assert first["sha256"] == hashlib.sha256(t.data).hexdigest()
    assert first["bytes"] == len(t.data) and first["download_status"] == "verified"
    assert first["open_status"] == "pending"
    assert first["studio_import"] == first["printer_receipt"] == first["print_start"] == "not_observed"
    assert t.token not in json.dumps(first)
    second = run(t)
    assert second["local_path"] == str(destination)
    assert sum(not r.url.path.endswith("/manifest") for r in t.calls) == 1
    assert not t.launched
    assert not list(t.root.rglob("*.partial")) and not list(t.root.rglob("*.tmp"))


def test_open_uses_fixed_safe_argv_after_durable_intent_and_never_repeats(sample, monkeypatch):
    t = sample
    seen = []

    def popen(argv, **kwargs):
        receipt = json.loads((t.root / "receipts/handoff-proof.json").read_text())
        assert receipt["open_status"] == "launch_unknown"
        assert Path(receipt["local_path"]).read_bytes() == t.data
        seen.append((argv, kwargs))
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(receiver.subprocess, "Popen", popen)
    result = run(t, open_studio=True, launcher=t.launcher)
    assert result["open_status"] == "process_started" and result["process_id"] == 12345
    assert seen[0][0] == [str(t.launcher), "--single-instance", result["local_path"]]
    assert seen[0][1]["shell"] is False
    assert seen[0][1]["stdin"] == receiver.subprocess.DEVNULL
    assert t.token not in " ".join(seen[0][0])
    assert run(t, open_studio=True, launcher=t.launcher)["open_status"] == "process_started"
    assert len(seen) == 1


def test_unknown_launch_survives_error_and_does_not_blindly_retry(sample, monkeypatch):
    t = sample
    seen = []

    def popen(*_args, **_kwargs):
        seen.append(True)
        raise OSError("sensitive " + t.url)

    monkeypatch.setattr(receiver.subprocess, "Popen", popen)
    with pytest.raises(ReceiverError, match="unknown") as error:
        run(t, open_studio=True, launcher=t.launcher)
    assert t.token not in str(error.value)
    assert run(t, open_studio=True, launcher=t.launcher)["open_status"] == "launch_unknown"
    assert len(seen) == 1


@pytest.mark.parametrize("change", ["hash", "short", "oversize", "unsafe_name", "boolean_size", "cross_origin", "wrong_file_route"])
def test_bad_manifest_or_payload_cannot_be_received(sample, change):
    t = sample
    m = t.state["manifest"]
    if change == "hash": m["sha256"] = "0" * 64
    elif change == "short": m["bytes"] -= 1
    elif change == "oversize": m["bytes"] = 129 * 1024 * 1024
    elif change == "unsafe_name": m["basename"] = "../../startup.ps1"
    elif change == "boolean_size": m["bytes"] = True
    elif change == "cross_origin": m["download_url"] = "https://other.example/file.gcode.3mf"
    else: m["download_url"] = t.origin + "/api/jobs"
    with pytest.raises(ReceiverError): run(t)
    assert not t.launched
    assert not list(t.root.rglob("*.gcode.3mf"))
    assert not list(t.root.rglob("*.partial"))


@pytest.mark.parametrize("where", ["manifest_status", "file_status"])
def test_redirects_never_follow_even_if_injected_client_defaults_to_follow(sample, where):
    t = sample
    t.state[where] = 302
    with pytest.raises(ReceiverError, match="redirects") as error: run(t)
    assert all(r.url.host == "trusted.example" for r in t.calls)
    assert t.token not in str(error.value)
    assert not t.launched


def test_expired_capability_http_error_remains_generic(sample):
    t = sample
    t.state["manifest_status"] = 404
    with pytest.raises(ReceiverError) as error: run(t)
    assert t.token not in str(error.value)
    assert len(t.calls) == 1 and not t.root.exists()


@pytest.mark.parametrize("url", [
    "https://evil.example/handoffs/" + "a" * 64,
    "https://user:pass@trusted.example/handoffs/" + "a" * 64,
    "https://trusted.example/handoffs/" + "a" * 64 + "?command=x",
    "https://trusted.example/handoffs/" + "a" * 64 + "#fragment",
    "https://trusted.example:8443/handoffs/" + "a" * 64,
    "https://trusted.example/handoffs/%2e%2e/secret",
    "file:///etc/passwd", "https://trusted.example/other",
])
def test_initial_origin_path_and_url_validation_precedes_http(sample, url):
    t = sample
    with pytest.raises(ReceiverError):
        receive(url, allowed_origin=t.origin, cache_root=t.root, client=t.client)
    assert not t.calls


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
def test_http_requires_explicit_exact_loopback_debug_option(sample, host):
    t = sample
    url = "http://" + host + "/handoffs/" + t.token
    t.state["manifest"]["download_url"] = url + "/file.gcode.3mf"
    with pytest.raises(ReceiverError):
        receive(url, allowed_origin="http://" + host, cache_root=t.root, client=t.client)
    assert receive(url, allowed_origin="http://" + host, cache_root=t.root,
                   client=t.client, allow_loopback_http=True)["download_status"] == "verified"
    for bad in ("localhost", "127.0.0.2", "trusted.example"):
        with pytest.raises(ReceiverError):
            receive("http://" + bad + "/handoffs/" + t.token, allowed_origin="http://" + bad,
                    cache_root=t.root, client=t.client, allow_loopback_http=True)


def test_existing_cache_change_missing_file_and_receipt_conflict_fail_closed(sample):
    t = sample
    receipt = run(t)
    cached = Path(receipt["local_path"])
    cached.write_bytes(b"changed")
    with pytest.raises(ReceiverError, match="integrity"): run(t)
    cached.unlink()
    with pytest.raises(ReceiverError, match="missing"): run(t)
    cached.write_bytes(t.data)
    t.state["manifest"]["sha256"] = "0" * 64
    with pytest.raises(ReceiverError, match="conflicts"): run(t)


def test_cached_symlink_is_not_followed(sample, tmp_path):
    t = sample
    receipt = run(t)
    outside = tmp_path / "protected"
    outside.write_bytes(t.data)
    cached = Path(receipt["local_path"])
    cached.unlink()
    cached.symlink_to(outside)
    with pytest.raises(ReceiverError): run(t)
    assert outside.read_bytes() == t.data


def test_duplicate_manifest_fields_and_unconfigured_launcher_are_rejected(sample):
    t = sample
    with pytest.raises(ReceiverError, match="configured"):
        run(t, open_studio=True)
    assert not t.calls
    t.state["raw_manifest"] = b'{"id":"a","id":"b"}'
    with pytest.raises(ReceiverError, match="Invalid manifest"): run(t)


def test_held_lock_blocks_concurrent_receive(sample):
    import fcntl
    t = sample
    t.root.joinpath("receipts").mkdir(parents=True)
    fd = os.open(t.root / "receipts/handoff-proof.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ReceiverError, match="already being received"): run(t)
    finally:
        os.close(fd)
