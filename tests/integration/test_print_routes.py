"""HTTP print-review guards, using disk-only jobs and a fully mocked controller.

No CAD, slicing, printer profile read, real transport or socket is authorized.
"""
from __future__ import annotations

import hashlib
import socket
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from justbuild.contracts import write_json
from justbuild.server import create_app

BASE = "http://127.0.0.1:18765"
JOB_ID = "c" * 32
MANIFEST_SHA = "a3" * 32
RISKS = ["lan_prototype_not_hardware_verified", "thermal_not_checked"]
HEADERS = {"Origin": BASE, "X-Justbuild-Intent": "print-review"}
PREPARE = "/api/jobs/" + JOB_ID + "/print/prepare"
PRINT = "/api/jobs/" + JOB_ID + "/print"
MONITOR = "/api/jobs/" + JOB_ID + "/print/monitor"
START = {"reviewed_manifest_sha256": MANIFEST_SHA, "acknowledged_risks": RISKS, "action": "start_print"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv("JUSTBUILD_ENABLE_LAN_PRINTING", raising=False)
    monkeypatch.delenv("JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP", raising=False)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("No real sockets in HTTP route tests"))
    apps = []

    def build(*, enabled=True, local=True, peer="127.0.0.1", base=BASE):
        index = len(apps)
        app = create_app(tmp_path / ("jobs-" + str(index)), local_pairing_enabled=local,
                         lan_printing_enabled=enabled, printer_profile=tmp_path / "never-created-private-profile.json")
        apps.append(app)
        calls = {"prepare": [], "submit": [], "monitor": []}

        def prepared(*args, **kwargs):
            calls["prepare"].append((args, kwargs))
            return {"status": "awaiting_review", "job_id": JOB_ID, "manifest_sha256": MANIFEST_SHA,
                    "manifest": {"risks": [{"id": value} for value in RISKS]}, "network_access_performed": False}

        def submitted(*args, **kwargs):
            calls["submit"].append((args, kwargs))
            return {"status": "needs_input", "job_id": JOB_ID, "state": "test_not_dispatched",
                    "dispatch_verified": False}

        def monitored(*args, **kwargs):
            calls["monitor"].append((args, kwargs))
            return {"status": "ok", "job_id": JOB_ID, "operation": "monitor_only", "print_command_sent": False}

        monkeypatch.setattr(app.state.print_controller, "prepare", prepared)
        monkeypatch.setattr(app.state.print_controller, "submit", submitted)
        monkeypatch.setattr(app.state.print_controller, "monitor", monitored)
        client = TestClient(app, base_url=base, client=(peer, 42000 + index))
        health = client.get("/api/health")
        assert health.status_code == 200
        token = client.cookies["justbuild_session"]
        owner = hashlib.sha256(token.encode()).hexdigest()
        directory = app.state.manager.root / JOB_ID
        directory.mkdir()
        job = {"schema_version": "0.1", "job_id": JOB_ID, "state": "ready_to_print", "label": "HTTP fixture only",
               "owner_sha256": owner, "review_token": "read-only-test-capability", "stage_results": {},
               "artifacts": [], "events": [], "issues": []}
        write_json(directory / "job.json", job)
        return SimpleNamespace(app=app, client=client, calls=calls, owner=owner, token=token,
                               directory=directory, health=health.json())

    yield SimpleNamespace(build=build, tmp_path=tmp_path)
    for app in apps:
        app.state.manager.pool.shutdown(wait=True)
    assert not (tmp_path / "never-created-private-profile.json").exists()


def assert_not_called(case):
    assert case.calls == {"prepare": [], "submit": [], "monitor": []}


def request_value(path):
    return dict(START) if path == PRINT else {}


def test_valid_prepare_only_prepares_review(rig):
    case = rig.build()
    assert case.health["lan_printing_enabled"] is True
    assert case.health["local_printer_setup_enabled"] is True
    response = case.client.post(PREPARE, json={}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["manifest_sha256"] == MANIFEST_SHA
    assert response.json()["network_access_performed"] is False
    assert case.calls["prepare"] == [((JOB_ID,), {})]
    assert case.calls["submit"] == case.calls["monitor"] == []


def test_valid_start_passes_exact_frozen_identity_risks_owner_and_approval(rig):
    case = rig.build()
    response = case.client.post(PRINT, json=START, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["dispatch_verified"] is False
    assert case.calls["submit"] == [((JOB_ID, MANIFEST_SHA, case.owner, RISKS), {
        "approval": {"approved": True, "action": "start_print", "reviewed_manifest_sha256": MANIFEST_SHA}})]
    assert case.calls["prepare"] == case.calls["monitor"] == []


def test_monitor_calls_only_monitor_with_owned_session(rig):
    case = rig.build()
    response = case.client.post(MONITOR, json={}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["operation"] == "monitor_only"
    assert case.calls["monitor"] == [((JOB_ID, case.owner), {})]
    assert case.calls["prepare"] == case.calls["submit"] == []
    # Monitoring may contact read-only status sources. A link/GET cannot trigger it.
    assert case.client.get(MONITOR).status_code == 405
    assert len(case.calls["monitor"]) == 1


@pytest.mark.parametrize("path", [PREPARE, PRINT, MONITOR])
@pytest.mark.parametrize("peer,host,extra", [
    ("192.168.1.3", "127.0.0.1:18765", {}),
    ("127.0.0.1", "public.example", {}),
    ("127.0.0.1", "localhost.evil:18765", {}),
    ("127.0.0.1", "127.0.0.1:18765", {"Forwarded": "for=192.168.1.3"}),
    ("127.0.0.1", "127.0.0.1:18765", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:18765", {"X-Forwarded-Proto": "https"}),
    ("127.0.0.1", "127.0.0.1:18765", {"X-Real-IP": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:18765", {"Sec-Fetch-Site": "cross-site"}),
])
def test_local_peer_host_and_proxy_required_before_controller(rig, path, peer, host, extra):
    case = rig.build(peer=peer)
    headers = {**HEADERS, "Host": host, "Origin": "http://" + host, **extra}
    response = case.client.post(path, json=request_value(path), headers=headers)
    assert response.status_code == 403
    assert_not_called(case)


@pytest.mark.parametrize("path", [PREPARE, PRINT, MONITOR])
@pytest.mark.parametrize("headers", [{}, {"Origin": BASE}, {"X-Justbuild-Intent": "print-review"},
    {**HEADERS, "X-Justbuild-Intent": "printer-setup"}, {**HEADERS, "Origin": "https://127.0.0.1:18765"},
    {**HEADERS, "Origin": "http://localhost:18765"}, {**HEADERS, "Origin": "https://unrelated.example"},
    {**HEADERS, "Origin": "null"}])
def test_origin_and_print_review_intent_required(rig, path, headers):
    case = rig.build()
    assert case.client.post(path, json=request_value(path), headers=headers).status_code == 403
    assert_not_called(case)


@pytest.mark.parametrize("path", [PREPARE, PRINT, MONITOR])
@pytest.mark.parametrize("mode", ["other_session", "shared_header", "shared_query", "missing_cookie"])
def test_other_sessions_and_share_capabilities_cannot_start_or_monitor(rig, path, mode):
    case = rig.build()
    other = TestClient(case.app, base_url=BASE, client=("127.0.0.1", 42111))
    headers = dict(HEADERS)
    if mode != "missing_cookie":
        other.get("/api/health")
    if mode == "shared_header":
        headers["X-Review-Token"] = "read-only-test-capability"
        readable = other.get("/api/jobs/" + JOB_ID, headers=headers)
        assert readable.status_code == 200 and readable.json()["can_edit"] is False
    if mode == "shared_query":
        path += "?view=read-only-test-capability"
    response = other.post(path, json=request_value(path.split("?", 1)[0]), headers=headers)
    assert response.status_code == 404
    assert_not_called(case)


@pytest.mark.parametrize("path", [PREPARE, PRINT, MONITOR])
def test_cli_job_without_owner_does_not_authorize_printing(rig, path):
    case = rig.build()
    case.app.state.manager.update(JOB_ID, owner_sha256=None)
    assert case.client.get("/api/jobs/" + JOB_ID).status_code == 200
    assert case.client.post(path, json=request_value(path), headers=HEADERS).status_code == 404
    assert_not_called(case)


@pytest.mark.parametrize("path", [PREPARE, PRINT, MONITOR])
@pytest.mark.parametrize("bad", ["unknown_field", "missing_field", "array", "media_type", "oversize", "query"])
def test_request_body_is_exact_json_and_queries_are_not_actions(rig, path, bad):
    case = rig.build()
    value = request_value(path)
    kwargs = {"headers": dict(HEADERS)}
    expected = 400
    if bad == "unknown_field":
        kwargs["json"] = {**value, "printer_command": "forbidden"}
    elif bad == "missing_field":
        if path == PRINT:
            kwargs["json"] = {"action": "start_print"}
        else:
            # Empty-object endpoints have no required fields; a null body is invalid.
            kwargs["json"] = None
            kwargs["content"] = b"null"
            kwargs["headers"]["Content-Type"] = "application/json"
    elif bad == "array":
        kwargs["json"] = []
    elif bad == "media_type":
        kwargs["content"] = b"{}"
        expected = 415
    elif bad == "oversize":
        kwargs["json"] = {"payload": "x" * 5000}
        expected = 413
    elif bad == "query":
        path += "?action=start_print"
        kwargs["json"] = value
        expected = 403
    response = case.client.post(path, **kwargs)
    assert response.status_code == expected, response.text
    assert_not_called(case)


@pytest.mark.parametrize("action", ["print", "prepare", "START_PRINT", "", None, True, ["start_print"]])
def test_start_requires_exact_explicit_action(rig, action):
    case = rig.build()
    response = case.client.post(PRINT, json={**START, "action": action}, headers=HEADERS)
    assert response.status_code == 409
    assert_not_called(case)


@pytest.mark.parametrize("hold,state", [({"reason": "renewed_review"}, "ready_to_print"),
    (True, "ready_to_print"), (None, "awaiting_confirmation"), (None, "review_incomplete"),
    (None, "review_ready"), (None, "failed"), (None, "slicing")])
def test_hold_and_nonready_state_block_start_before_controller(rig, hold, state):
    case = rig.build()
    case.app.state.manager.update(JOB_ID, print_hold=hold, state=state)
    response = case.client.post(PRINT, json=START, headers=HEADERS)
    assert response.status_code == 409
    assert_not_called(case)


def test_prepare_and_monitor_do_not_clear_review_hold(rig):
    case = rig.build()
    hold = {"reason": "redesign_requires_review"}
    case.app.state.manager.update(JOB_ID, print_hold=hold)
    assert case.client.post(PREPARE, json={}, headers=HEADERS).status_code == 200
    assert case.client.post(MONITOR, json={}, headers=HEADERS).status_code == 200
    assert case.app.state.manager.get(JOB_ID)["print_hold"] == hold
    assert case.calls["submit"] == []


def test_default_disabled_preserves_old_501_and_hides_new_control_routes(rig):
    case = rig.build(enabled=None, local=None)
    assert case.health["lan_printing_enabled"] is False
    assert case.health["local_printer_setup_enabled"] is False
    assert case.client.post(PRINT, json=START, headers=HEADERS).status_code == 501
    assert case.client.post(PREPARE, json={}, headers=HEADERS).status_code == 404
    assert case.client.post(MONITOR, json={}, headers=HEADERS).status_code == 404
    assert_not_called(case)


@pytest.mark.parametrize("path", [PREPARE, PRINT, MONITOR])
def test_unknown_job_rejected_before_controller(rig, path):
    case = rig.build()
    assert case.client.post(path.replace(JOB_ID, "d" * 32), json=request_value(path), headers=HEADERS).status_code == 404
    assert_not_called(case)
