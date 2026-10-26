"""Local-only pairing security/contract tests: no real printer sockets or account."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from justbuild import printer_setup as setup

BASE = "http://127.0.0.1:18765"
HEADERS = {"Origin": BASE, "X-Justbuild-Intent": "printer-setup"}
PINS = {"mqtt_certificate_sha256": "a" * 64, "ftp_certificate_sha256": "b" * 64}
HOST = "172.20.10.2"
SERIAL = "PRIVATE_SN_012345"
CODE = "X9abc123"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    calls = {"probe": [], "save": [], "check": [], "status": []}
    private_path = tmp_path / "private" / "a1.json"
    profile = {"status": "configured", "host": HOST, "model": "Bambu Lab A1", "serial_masked": "***2345",
               "firmware": "01.07.00.00", "mqtt_pin_present": True, "ftp_pin_present": True,
               "developer_mode_confirmed": True, "configured": True, "offline_ready": True,
               "network_check_performed": False, "dispatch_verified": False}

    def probe(host, **kwargs):
        calls["probe"].append((host, kwargs))
        return {"services": {"mqtt": {"port": 8883, "status": "observed", "certificate_sha256": PINS["mqtt_certificate_sha256"]},
                             "ftp": {"port": 990, "status": "observed", "certificate_sha256": PINS["ftp_certificate_sha256"]}}}

    def save(values, *, path=None):
        calls["save"].append((dict(values), path))
        return dict(profile, access_code=CODE, serial=SERIAL)

    def status(*, path=None):
        calls["status"].append(path)
        return dict(profile, serial=SERIAL, access_code=CODE)

    def check(*, profile_path=None):
        calls["check"].append(profile_path)
        return {"status": "ok", "read_only": True, "mqtt_authenticated": True, "ftps_authenticated": True,
                "profile": dict(profile, access_code=CODE, serial=SERIAL), "secret": CODE,
                "printer": {"identity_verified": True, "state": "IDLE", "serial": SERIAL}, "issues": []}

    monkeypatch.setattr(setup.lan, "probe_lan_tls", probe)
    monkeypatch.setattr(setup.lan, "save_lan_profile", save)
    monkeypatch.setattr(setup.lan, "lan_profile_status", status)
    monkeypatch.setattr(setup.lan, "check_lan_connection", check)

    def app_factory(enabled=True, *, session=True):
        app = FastAPI()
        if session:
            @app.middleware("http")
            async def owner(request: Request, call_next):
                token = request.cookies.get("justbuild_session", "")
                request.state.owner = hashlib.sha256(token.encode()).hexdigest()
                return await call_next(request)
        setup.install_local_printer_routes(app, enabled=enabled, profile_path=private_path)
        app.mount("/static", StaticFiles(directory=Path(setup.__file__).parent / "web"), name="static")
        return app

    def client_factory(app=None, *, peer="127.0.0.1", base=BASE, cookie="1" * 64):
        cookies = {"justbuild_session": cookie} if cookie else {}
        return TestClient(app or app_factory(), base_url=base, client=(peer, 43210), cookies=cookies)

    return SimpleNamespace(calls=calls, profile=profile, path=private_path,
                           app=app_factory, client=client_factory)


def probe(client):
    response = client.post("/api/printers/local/probe", json={"host": HOST, "developer_mode_confirmed": True}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def pair_value(observation):
    return {"probe_id": observation["probe_id"], "host": HOST, **PINS,
            "serial": SERIAL, "access_code": CODE, "firmware": "01.07.00.00",
            "developer_mode_confirmed": True, "fingerprint_confirmed": True}


@pytest.mark.parametrize("enabled", [False, None, 1, "true", "1"])
def test_default_off_and_literal_boolean(rig, enabled):
    client = rig.client(rig.app(enabled))
    for path in ("/printer-setup", "/printer-setup.js", "/printer-setup.css", "/api/printers/local"):
        assert client.get(path).status_code == 404
    assert client.post("/api/printers/local/probe", json={}, headers=HEADERS).status_code == 404
    assert not any(rig.calls.values())


@pytest.mark.parametrize("peer,host,headers", [
    ("192.168.1.3", "127.0.0.1:18765", {}),
    ("127.0.0.1", "evil.example", {}),
    ("127.0.0.1", "localhost.evil:18765", {}),
    ("127.0.0.1", "127.0.0.1.evil:18765", {}),
    ("127.0.0.1", "127.0.0.1:0", {}),
    ("127.0.0.1", "localhost", {"Forwarded": "for=192.168.1.2"}),
    ("127.0.0.1", "localhost", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", "localhost", {"X-Forwarded-Proto": "https"}),
    ("127.0.0.1", "localhost", {"X-Real-IP": "127.0.0.1"}),
    ("127.0.0.1", "localhost", {"Origin": "https://evil.example"}),
    ("127.0.0.1", "localhost", {"Sec-Fetch-Site": "cross-site"}),
])
def test_local_host_peer_and_proxy_guards(rig, peer, host, headers):
    client = rig.client(peer=peer)
    assert client.get("/printer-setup", headers={"Host": host, **headers}).status_code == 403
    assert client.get("/api/printers/local", headers={"Host": host, **headers}).status_code == 403
    assert not any(rig.calls.values())


@pytest.mark.parametrize("peer,base", [("127.0.0.1", BASE), ("::1", "http://[::1]:18765"), ("127.0.0.1", "http://localhost:18765")])
def test_canonical_pages_assets_and_headers(rig, peer, base):
    # Installed Starlette test transport cannot parse an IPv6 URL authority;
    # retain the real ASGI IPv6 peer and Host while using its IPv4 transport URL.
    client = rig.client(peer=peer, base=BASE if peer == "::1" else base)
    if peer == "::1":
        client.headers["Host"] = "[::1]:18765"
    for path in ("/printer-setup", "/printer-setup.js", "/printer-setup.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert 'type="password"' in client.get("/printer-setup").text
    for path in ("/static/printer-setup.html", "/static/printer-setup.js", "/static/printer-setup.css"):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Origin": BASE}, {"X-Justbuild-Intent": "printer-setup"},
    {**HEADERS, "Origin": "http://localhost:18765"}, {**HEADERS, "Origin": "https://127.0.0.1:18765"},
    {**HEADERS, "Origin": "null"}, {**HEADERS, "X-Justbuild-Intent": "other"}])
def test_post_requires_same_origin_and_explicit_intent(rig, headers):
    response = rig.client().post("/api/printers/local/probe", json={"host": HOST, "developer_mode_confirmed": True}, headers=headers)
    assert response.status_code == 403
    assert not any(rig.calls.values())


@pytest.mark.parametrize("cookie,session", [(None, True), ("invalid", True), ("2" * 64, False)])
def test_post_requires_existing_session_and_owner(rig, cookie, session):
    response = rig.client(rig.app(session=session), cookie=cookie).post("/api/printers/local/probe", json={}, headers=HEADERS)
    assert response.status_code == 403
    assert not any(rig.calls.values())


@pytest.mark.parametrize("host", ["8.8.8.8", "127.0.0.1", "169.254.1.2", "100.64.0.1", "224.0.0.1", "0.0.0.0", "::1", "fd00::1", "localhost", "10.1.2.3:8883", "012.1.2.3", 123])
def test_probe_rejects_non_rfc1918_ipv4_without_network(rig, host):
    response = rig.client().post("/api/printers/local/probe", json={"host": host, "developer_mode_confirmed": True}, headers=HEADERS)
    assert response.status_code == 400
    assert not rig.calls["probe"]


def test_probe_fixed_ports_and_unverified_discovery(rig):
    result = probe(rig.client())
    assert rig.calls["probe"] == [(HOST, {"mqtt_port": 8883, "ftp_port": 990})]
    assert result["expires_in_seconds"] == 300
    assert result["identity_verified"] is result["credentials_sent"] is result["print_command_sent"] is False
    assert result["mqtt_certificate_sha256"] == PINS["mqtt_certificate_sha256"]
    assert not rig.calls["save"]


@pytest.mark.parametrize("alter", [{"developer_mode_confirmed": False}, {"developer_mode_confirmed": "true"}, {"mqtt_port": 1883}])
def test_probe_requires_device_mode_and_no_client_ports(rig, alter):
    body = {"host": HOST, "developer_mode_confirmed": True, **alter}
    response = rig.client().post("/api/printers/local/probe", json=body, headers=HEADERS)
    assert response.status_code == 400
    assert not rig.calls["probe"]


def test_incomplete_probe_never_returns_usable_nonce(rig, monkeypatch):
    monkeypatch.setattr(setup.lan, "probe_lan_tls", lambda *a, **kw: {"services": {"mqtt": {"status": "unavailable"}}})
    response = rig.client().post("/api/printers/local/probe", json={"host": HOST, "developer_mode_confirmed": True}, headers=HEADERS)
    assert response.status_code == 409
    assert "probe_id" not in response.json()


def test_successful_pair_is_nonce_bound_private_and_not_printed(rig):
    client = rig.client(); body = pair_value(probe(client))
    response = client.post("/api/printers/local/pair", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "paired"
    assert response.json()["dispatch_verified"] is False
    assert CODE not in response.text and SERIAL not in response.text
    assert len(rig.calls["save"]) == 1
    values, path = rig.calls["save"][0]
    assert path == rig.path
    assert values == {key: body[key] for key in ("host", "serial", "access_code", "firmware", "developer_mode_confirmed")} | PINS | {"mqtt_port": 8883, "ftp_port": 990, "authorization_mode": "developer_lan"}
    assert client.post("/api/printers/local/pair", json=body, headers=HEADERS).status_code == 409
    assert not rig.calls["check"]


@pytest.mark.parametrize("alter", [{"host": "192.168.1.2"}, {"mqtt_certificate_sha256": "c" * 64},
    {"ftp_certificate_sha256": "c" * 64}, {"mqtt_certificate_sha256": "中" * 64},
    {"fingerprint_confirmed": False}, {"fingerprint_confirmed": "true"}, {"developer_mode_confirmed": False},
    {"probe_id": "fabricated"}])
def test_pair_refuses_changed_host_pins_and_missing_confirmation(rig, alter):
    client = rig.client(); body = {**pair_value(probe(client)), **alter}
    response = client.post("/api/printers/local/pair", json=body, headers=HEADERS)
    assert response.status_code in (400, 409)
    assert CODE not in response.text
    assert not rig.calls["save"]


def test_nonce_cannot_cross_sessions_or_survive_new_probe(rig):
    app = rig.app(); first = rig.client(app, cookie="1" * 64); second = rig.client(app, cookie="2" * 64)
    old = probe(first); body = pair_value(old)
    assert second.post("/api/printers/local/pair", json=body, headers=HEADERS).status_code == 409
    probe(first)
    assert first.post("/api/printers/local/pair", json=body, headers=HEADERS).status_code == 409
    assert not rig.calls["save"]


def test_probe_expires_in_five_minutes(rig, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(setup, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = rig.client(); body = pair_value(probe(client)); clock[0] += 301
    assert client.post("/api/printers/local/pair", json=body, headers=HEADERS).status_code == 409
    assert not rig.calls["save"]


def test_status_and_read_only_check_never_expose_credentials(rig):
    client = rig.client(); response = client.get("/api/printers/local")
    assert response.status_code == 200
    assert response.json()["profile"]["network_check_performed"] is False
    assert CODE not in response.text and SERIAL not in response.text
    assert rig.calls["status"] == [rig.path]
    response = client.post("/api/printers/local/check", json={}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert response.json()["print_command_sent"] is response.json()["dispatch_verified"] is False
    assert CODE not in response.text and SERIAL not in response.text
    assert rig.calls["check"] == [rig.path]
    assert not rig.calls["probe"] and not rig.calls["save"]


def test_request_size_type_unknown_fields_and_query_guard(rig):
    client = rig.client()
    assert client.post("/api/printers/local/probe", content="{}", headers=HEADERS).status_code == 415
    assert client.post("/api/printers/local/probe", json={"host": "x" * 5000}, headers=HEADERS).status_code == 413
    assert client.post("/api/printers/local/probe?access_code=private", json={}, headers=HEADERS).status_code == 403
    assert client.post("/api/printers/local/check", json={"print": True}, headers=HEADERS).status_code == 400
    assert not any(rig.calls.values())


def test_errors_redact_lower_level_messages_and_unknown_fields(rig, monkeypatch):
    def fail(*a, **kw):
        raise setup.lan.LanError("LAN_ACCESS_CODE", CODE + SERIAL)
    client = rig.client()
    monkeypatch.setattr(setup.lan, "save_lan_profile", fail)
    response = client.post("/api/printers/local/pair", json=pair_value(probe(client)), headers=HEADERS)
    assert response.status_code == 400
    assert CODE not in response.text and SERIAL not in response.text
    monkeypatch.setattr(setup.lan, "check_lan_connection", lambda **kw: {
        "status": "needs_input", "access_code": CODE, "issues": [{"code": "LAN_ACCESS_CODE", "message": CODE + SERIAL}]})
    response = client.post("/api/printers/local/check", json={}, headers=HEADERS)
    assert CODE not in response.text and SERIAL not in response.text


def test_busy_connection_result_is_distinct_from_pairing_failure(rig, monkeypatch):
    monkeypatch.setattr(setup.lan, "check_lan_connection", lambda **kw: {
        "status": "needs_input", "connection_verified": True, "ready_for_new_print": False,
        "mqtt_authenticated": True, "ftps_authenticated": True, "certificate_pins_verified": True,
        "printer": {"identity_verified": True, "firmware_matches": True, "state": "RUNNING", "progress_percent": 91},
        "access_code": CODE, "serial": SERIAL,
        "issues": [{"code": "LAN_NOT_IDLE", "message": CODE + SERIAL}]})
    response = rig.client().post("/api/printers/local/check", json={}, headers=HEADERS)
    assert response.status_code == 200
    value = response.json()
    assert value["connection_verified"] is True and value["ready_for_new_print"] is False
    assert value["printer"]["state"] == "RUNNING"
    assert value["print_command_sent"] is False and value["dispatch_verified"] is False
    assert "连接正常" in value["issues"][0]["message"]
    assert CODE not in response.text and SERIAL not in response.text
