"""HTTP transfer evidence only; no desktop application or printer is invoked."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from justbuild.handoff import HandoffStore
from justbuild.handoff_server import create_handoff_app


@pytest.fixture
def transfer(tmp_path):
    job_id = "d" * 32
    folder = tmp_path / "jobs" / job_id / "slicing"
    folder.mkdir(parents=True)
    artifact = folder / "enclosure.gcode.3mf"
    data = b"PK\x03\x04" + bytes(range(256)) * 4
    artifact.write_bytes(data)
    item = {"kind": "sliced_3mf", "path": str(artifact),
            "sha256": hashlib.sha256(data).hexdigest()}
    result = {"job_id": job_id, "status": "ok", "simulation": False,
              "artifacts": [item], "sliced_artifact": deepcopy(item),
              "printer": {"model": "Bambu Lab A1", "id": "private-printer-id"},
              "settings": {"material": "PLA", "nozzle_diameter_mm": 0.4,
                           "profiles": {"process": {"id": "<script>profile</script>"}}}}
    job = {"job_id": job_id, "owner_sha256": None, "state": "ready_to_print",
           "label": '<img src=x onerror="alert(1)">真实板', "synthetic": False,
           "stage_results": {"verifying": {"result": {"verdict": "pass"}},
                             "slicing": {"path": "slicing/print_job.json", "result": result}}}
    manager = SimpleNamespace(root=tmp_path / "jobs", get=lambda _: deepcopy(job))
    clock = [1700000000.0]
    store = HandoffStore(tmp_path / "handoffs", clock=lambda: clock[0])
    info = store.create(manager, job_id, "https://download.example", ttl_seconds=30)
    app = create_handoff_app(store)
    return SimpleNamespace(client=TestClient(app), store=store, info=info,
                           path="/handoffs/" + info["token"], data=data,
                           clock=clock, artifact=artifact, app=app)


def test_exact_http_bytes_hash_filename_and_head(transfer):
    t = transfer
    response = t.client.get(t.path + "/file.gcode.3mf")
    assert response.status_code == 200
    assert response.content == t.data
    assert hashlib.sha256(response.content).hexdigest() == t.info["sha256"]
    assert int(response.headers["content-length"]) == len(t.data)
    assert response.headers["x-artifact-sha256"] == t.info["sha256"]
    assert t.info["basename"] in response.headers["content-disposition"]
    assert response.headers["accept-ranges"] == "bytes"
    head = t.client.head(t.path + "/file.gcode.3mf", headers={"Range": "bytes=0-3"})
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == response.headers["content-length"]
    assert head.headers["etag"] == response.headers["etag"]


@pytest.mark.parametrize("requested,start,end", [
    ("bytes=0-3", 0, 3), ("bytes=7-", 7, None), ("bytes=-12", -12, None),
    ("bytes=5-999999", 5, None), ("bytes=-999999", 0, None),
])
def test_single_ranges_return_the_actual_slice(transfer, requested, start, end):
    t = transfer
    response = t.client.get(t.path + "/file.gcode.3mf", headers={"Range": requested})
    assert response.status_code == 206
    assert response.content == t.data[start:end + 1 if end is not None else None]
    assert int(response.headers["content-length"]) == len(response.content)
    actual_start = start if start >= 0 else len(t.data) + start
    actual_end = end if end is not None else len(t.data) - 1
    assert response.headers["content-range"] == f"bytes {actual_start}-{actual_end}/{len(t.data)}"


@pytest.mark.parametrize("requested", [
    "bytes=999999-", "bytes=8-2", "bytes=-0", "bytes=-", "bytes=a-b",
    "bytes=0-1,3-4", "items=0-1", "bytes=" + "1" * 90 + "-",
])
def test_invalid_or_multiple_ranges_are_416(transfer, requested):
    t = transfer
    response = t.client.get(t.path + "/file.gcode.3mf", headers={"Range": requested})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(t.data)}"
    assert t.info["token"] not in response.text


def test_if_range_mismatch_returns_complete_file(transfer):
    t = transfer
    response = t.client.get(t.path + "/file.gcode.3mf",
                            headers={"Range": "bytes=0-3", "If-Range": '"other-version"'})
    assert response.status_code == 200 and response.content == t.data
    assert "content-range" not in response.headers
    response = t.client.get(t.path + "/file.gcode.3mf",
                            headers={"Range": "bytes=0-3", "If-Range": '"' + t.info["sha256"] + '"'})
    assert response.status_code == 206 and response.content == t.data[:4]


def test_landing_is_escaped_self_contained_and_honest(transfer):
    t = transfer
    response = t.client.get(t.path)
    assert response.status_code == 200
    assert '<img src=x' not in response.text
    assert "&lt;img src=x" in response.text
    assert "<script>profile</script>" not in response.text
    assert "&lt;script&gt;profile&lt;/script&gt;" in response.text
    assert "真实板卡任务" in response.text
    for text in ("Windows：通过已安装的 justbuild 接收器导入", "Windows：Bambu Studio 原生导入", "macOS：自动导入 Bambu Studio",
                 "不会启动打印", "不能绕过", t.info["sha256"], t.info["expires_at"], "Bambu Lab A1"):
        assert text in response.text
    assert 'href="bambustudio://open?file=' in response.text
    assert 'href="justbuild://open?handoff=' in response.text
    assert 'href="bambustudioopen://' in response.text
    assert "<script" not in response.text
    assert 'src="http' not in response.text
    head = t.client.head(t.path)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == response.headers["content-length"]


def test_manifest_excludes_private_paths_and_has_no_print_permission(transfer):
    t = transfer
    response = t.client.get(t.path + "/manifest")
    assert response.status_code == 200
    info = response.json()
    assert info["grants_print_permission"] is False
    assert info["desktop_uri_dispatch_verified"] is False
    assert info["permissions"] == ["download_sliced_3mf"]
    for private in (str(t.artifact), "private-printer-id", "owner_sha256", "token_sha256", "seal"):
        assert private not in response.text
    head = t.client.head(t.path + "/manifest")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == response.headers["content-length"]


@pytest.mark.parametrize("suffix", ["", "/manifest", "/file.gcode.3mf"])
def test_security_headers_and_expiry_for_every_resource(transfer, suffix):
    t = transfer
    response = t.client.get(t.path + suffix)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "script-src 'none'" in response.headers["content-security-policy"]
    t.clock[0] += 30
    expired = t.client.get(t.path + suffix)
    assert expired.status_code == 404
    assert expired.text == "Handoff unavailable"
    assert expired.headers["cache-control"] == "no-store"
    assert t.info["token"] not in expired.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_no_mutations_on_any_path(transfer, method):
    t = transfer
    for path in (t.path, t.path + "/file.gcode.3mf", "/api/jobs"):
        response = t.client.request(method, path, json={"start_print": True})
        assert response.status_code == 405
        assert response.headers["allow"] == "GET, HEAD"
        assert t.info["token"] not in response.text


def test_unknown_tokens_no_cad_api_no_directory_access_and_no_redirect(transfer):
    t = transfer
    for path in ("/handoffs/" + "0" * 64, "/handoffs/not-a-token/manifest",
                 "/api/jobs", "/api/health", "/docs", "/openapi.json", "/redoc",
                 t.path + "/../../source.step", t.path + "/file.gcode.3mf/", "/"):
        response = t.client.get(path, follow_redirects=False)
        assert response.status_code == 404, path
        assert response.text == "Handoff unavailable"


def test_changed_artifact_and_store_errors_do_not_leak(transfer, monkeypatch):
    t = transfer
    t.artifact.write_bytes(b"different payload")
    for suffix in ("", "/manifest", "/file.gcode.3mf"):
        response = t.client.get(t.path + suffix)
        assert response.status_code == 404 and response.text == "Handoff unavailable"
        assert str(t.artifact) not in response.text
    t.artifact.write_bytes(t.data)

    def failed(_token):
        raise RuntimeError("private path /home/secrets; token " + t.info["token"])

    monkeypatch.setattr(t.store, "payload", failed)
    response = t.client.get(t.path + "/file.gcode.3mf")
    assert response.status_code == 404 and response.text == "Handoff unavailable"


def test_http_uses_validated_bytes_without_a_path_reopen(transfer, monkeypatch):
    t = transfer
    original = t.store.payload

    def replace_after_read(token):
        data = original(token)
        t.artifact.write_bytes(b"changed after payload validation")
        return data

    monkeypatch.setattr(t.store, "payload", replace_after_read)
    response = t.client.get(t.path + "/file.gcode.3mf")
    assert response.status_code == 200 and response.content == t.data
    assert t.artifact.read_bytes() != response.content
