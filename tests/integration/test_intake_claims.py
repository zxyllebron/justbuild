"""Disk-backed ownership claims; no CAD, network or printer operations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import socket
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from justbuild import intake_claims as claims
from justbuild.contracts import read_json, write_json
from justbuild.orchestrator import JobManager

JOB = "e" * 32
OWNER = "1" * 64
OTHER = "2" * 64


@pytest.fixture
def case(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Ownership claims must not run geometry, pipeline or printer/network operations")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(JobManager, "confirm", forbidden)
    monkeypatch.setattr(JobManager, "run", forbidden)
    monkeypatch.setattr(JobManager, "_stage", forbidden)
    manager = JobManager(tmp_path / "jobs")
    directory = manager.root / JOB
    directory.mkdir()
    job = {"schema_version": "0.1", "job_id": JOB, "state": "awaiting_confirmation",
           "owner_sha256": None, "intake_claim_eligible": True, "user_requirements": "Explicit user requirements",
           "artifacts": [], "stage_results": {}, "events": [], "issues": []}
    write_json(directory / "job.json", job)
    yield SimpleNamespace(manager=manager, directory=directory)
    manager.pool.shutdown(wait=True)


def test_issue_persists_only_hash_and_bounded_expiry(case, monkeypatch):
    monkeypatch.setattr(claims, "time", SimpleNamespace(time=lambda: 1000.0))
    token = claims.issue_intake_claim(case.manager, JOB)
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    saved = case.manager.get(JOB)
    assert saved["intake_claim"] == {"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                                     "issued_at": 1000.0, "expires_at": 4600.0, "scope": "local_intake_owner"}
    assert token not in (case.directory / "job.json").read_text()
    assert saved["owner_sha256"] is None and saved["state"] == "awaiting_confirmation"
    assert saved["stage_results"] == {} and saved["artifacts"] == []


def test_claim_binds_owner_and_readonly_share_and_consumes_once(case):
    token = claims.issue_intake_claim(case.manager, JOB, ttl_seconds=90)
    result = claims.claim_intake_job(case.manager, JOB, token, OWNER)
    assert result == case.manager.get(JOB)
    assert result["owner_sha256"] == OWNER
    assert result["intake_claim"] is None and result["intake_claim_eligible"] is False
    assert re.fullmatch(r"[0-9a-f]{48}", result["review_token"])
    assert result["review_token"] != token
    share = urlparse(result["share_url"])
    assert share.path == "/" and parse_qs(share.query) == {"job": [JOB], "view": [result["review_token"]]}
    assert "claim" not in share.query
    assert result["state"] == "awaiting_confirmation" and result["user_requirements"] == "Explicit user requirements"
    assert result["artifacts"] == [] and result["stage_results"] == {} and result["events"] == []
    assert token not in (case.directory / "job.json").read_text()
    before = (case.directory / "job.json").read_bytes()
    for claimant in (OWNER, OTHER):
        with pytest.raises(claims.IntakeClaimError):
            claims.claim_intake_job(case.manager, JOB, token, claimant)
    assert before == (case.directory / "job.json").read_bytes()


def test_wrong_token_does_not_consume_or_change_any_field(case):
    token = claims.issue_intake_claim(case.manager, JOB)
    wrong = ("0" if token[0] != "0" else "1") + token[1:]
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError, match="invalid"):
        claims.claim_intake_job(case.manager, JOB, wrong, OWNER)
    assert before == (case.directory / "job.json").read_bytes()
    assert claims.claim_intake_job(case.manager, JOB, token, OWNER)["owner_sha256"] == OWNER


@pytest.mark.parametrize("elapsed", [60, 61])
def test_expired_claim_cannot_bind_or_change_job(case, monkeypatch, elapsed):
    clock = [1000.0]
    monkeypatch.setattr(claims, "time", SimpleNamespace(time=lambda: clock[0]))
    token = claims.issue_intake_claim(case.manager, JOB, ttl_seconds=60)
    before = (case.directory / "job.json").read_bytes()
    clock[0] += elapsed
    with pytest.raises(claims.IntakeClaimError) as raised:
        claims.claim_intake_job(case.manager, JOB, token, OWNER)
    assert raised.value.code == "INTAKE_CLAIM_EXPIRED"
    assert before == (case.directory / "job.json").read_bytes()


@pytest.mark.parametrize("ttl", [0, -1, 3601, True, 1.5, "60", None])
def test_invalid_or_over_one_hour_ttl_rejected_without_write(case, ttl):
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError):
        claims.issue_intake_claim(case.manager, JOB, ttl_seconds=ttl)
    assert before == (case.directory / "job.json").read_bytes()


@pytest.mark.parametrize("token,owner", [("", OWNER), ("a" * 63, OWNER), ("A" * 64, OWNER),
    (None, OWNER), (123, OWNER), ("中" * 64, OWNER), ("a" * 64, ""),
    ("a" * 64, "G" * 64), ("a" * 64, None), ("a" * 64, [OWNER])])
def test_malformed_token_or_owner_never_mutates_job(case, token, owner):
    claims.issue_intake_claim(case.manager, JOB)
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError):
        claims.claim_intake_job(case.manager, JOB, token, owner)
    assert before == (case.directory / "job.json").read_bytes()


@pytest.mark.parametrize("updates", [{"intake_claim_eligible": False}, {"intake_claim_eligible": None},
    {"intake_claim_eligible": "true"}, {"owner_sha256": OWNER}, {"state": "ready_to_print"},
    {"state": "failed"}, {"state": "generating"}, {"print_hold": {"reason": "real_board_redesign_review"}}])
def test_issue_cannot_claim_old_owned_nonintake_or_held_job(case, updates):
    case.manager.update(JOB, **updates)
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError):
        claims.issue_intake_claim(case.manager, JOB)
    assert before == (case.directory / "job.json").read_bytes()


def test_old_job_without_explicit_marker_cannot_issue_or_claim(case):
    job = case.manager.get(JOB); job.pop("intake_claim_eligible")
    write_json(case.directory / "job.json", job)
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError):
        claims.issue_intake_claim(case.manager, JOB)
    with pytest.raises(claims.IntakeClaimError):
        claims.claim_intake_job(case.manager, JOB, "a" * 64, OWNER)
    assert before == (case.directory / "job.json").read_bytes()


@pytest.mark.parametrize("updates", [{"print_hold": {"reason": "preserve hold"}}, {"owner_sha256": OTHER},
    {"state": "ready_to_print"}, {"intake_claim_eligible": False}])
def test_eligibility_is_rechecked_at_consumption_and_hold_is_never_cleared(case, updates):
    token = claims.issue_intake_claim(case.manager, JOB)
    case.manager.update(JOB, **updates)
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError):
        claims.claim_intake_job(case.manager, JOB, token, OWNER)
    assert before == (case.directory / "job.json").read_bytes()


def test_issuer_cannot_replace_live_challenge(case):
    token = claims.issue_intake_claim(case.manager, JOB)
    before = (case.directory / "job.json").read_bytes()
    with pytest.raises(claims.IntakeClaimError) as raised:
        claims.issue_intake_claim(case.manager, JOB)
    assert raised.value.code == "INTAKE_CLAIM_ALREADY_ISSUED"
    assert before == (case.directory / "job.json").read_bytes()
    assert claims.claim_intake_job(case.manager, JOB, token, OWNER)["owner_sha256"] == OWNER


def test_needs_input_is_claimable_but_does_not_advance_pipeline(case):
    case.manager.update(JOB, state="needs_input")
    token = claims.issue_intake_claim(case.manager, JOB)
    result = claims.claim_intake_job(case.manager, JOB, token, OWNER)
    assert result["state"] == "needs_input"
    assert result["stage_results"] == {} and result["artifacts"] == []


def test_concurrent_sessions_have_exactly_one_winner(case):
    token = claims.issue_intake_claim(case.manager, JOB)
    def attempt(owner):
        try:
            return claims.claim_intake_job(case.manager, JOB, token, owner)["owner_sha256"]
        except claims.IntakeClaimError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [OWNER, OTHER]))
    assert len([owner for owner in results if owner is not None]) == 1
    winner = next(owner for owner in results if owner is not None)
    assert case.manager.get(JOB)["owner_sha256"] == winner
    assert case.manager.get(JOB)["intake_claim"] is None
    assert token not in (case.directory / "job.json").read_text()


def test_unknown_job_is_generic_error_without_creating_anything(case):
    before = sorted(path.name for path in case.manager.root.iterdir())
    with pytest.raises(claims.IntakeClaimError):
        claims.issue_intake_claim(case.manager, "f" * 32)
    with pytest.raises(claims.IntakeClaimError):
        claims.claim_intake_job(case.manager, "f" * 32, "a" * 64, OWNER)
    assert before == sorted(path.name for path in case.manager.root.iterdir())


@pytest.fixture
def http_case(case):
    from fastapi.testclient import TestClient
    from justbuild.server import create_app
    app = create_app(case.manager.root, local_pairing_enabled=False, lan_printing_enabled=False,
                     printer_profile=case.directory / "never-created-profile.json")
    client = TestClient(app, base_url="http://127.0.0.1:18765", client=("127.0.0.1", 43201))
    client.get("/api/health")
    token = claims.issue_intake_claim(app.state.manager, JOB)
    yield SimpleNamespace(app=app, client=client, token=token, path="/api/jobs/" + JOB + "/claim",
                          headers={"Origin": "http://127.0.0.1:18765", "X-Justbuild-Intent": "intake-claim"},
                          directory=case.directory)
    client.close()
    app.state.manager.pool.shutdown(wait=True)
    assert not (case.directory / "never-created-profile.json").exists()


def assert_public_claim_scrubbed(response, token):
    assert response.status_code == 200, response.text
    value = response.json()
    assert not any(key.startswith("intake_claim") for key in value)
    assert "owner_sha256" not in value and "review_token" not in value
    assert token not in response.text
    assert hashlib.sha256(token.encode()).hexdigest() not in response.text
    return value


def test_http_claim_binds_owner_once_and_never_exposes_claim_state(http_case):
    case = http_case
    before = case.client.get("/api/jobs/" + JOB)
    assert assert_public_claim_scrubbed(before, case.token)["can_edit"] is False
    wrong = ("0" if case.token[0] != "0" else "1") + case.token[1:]
    bad = case.client.post(case.path, json={"token": wrong}, headers=case.headers)
    assert bad.status_code == 409
    response = case.client.post(case.path, json={"token": case.token}, headers=case.headers)
    value = assert_public_claim_scrubbed(response, case.token)
    assert value["can_edit"] is True and value["state"] == "awaiting_confirmation"
    owner = hashlib.sha256(case.client.cookies["justbuild_session"].encode()).hexdigest()
    saved = case.app.state.manager.get(JOB)
    assert saved["owner_sha256"] == owner
    assert saved["intake_claim"] is None and saved["intake_claim_eligible"] is False
    assert case.client.post(case.path, json={"token": case.token}, headers=case.headers).status_code == 409
    assert assert_public_claim_scrubbed(case.client.get("/api/jobs/" + JOB), case.token)["can_edit"] is True


@pytest.mark.parametrize("peer,host,extra", [
    ("192.168.1.10", "127.0.0.1:18765", {}),
    ("127.0.0.1", "public.example", {}),
    ("127.0.0.1", "127.0.0.1:18765", {"Forwarded": "for=192.168.1.3"}),
    ("127.0.0.1", "127.0.0.1:18765", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:18765", {"X-Real-IP": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:18765", {"Origin": "https://127.0.0.1:18765"}),
    ("127.0.0.1", "127.0.0.1:18765", {"Origin": "https://unrelated.example"}),
    ("127.0.0.1", "127.0.0.1:18765", {"X-Justbuild-Intent": "print-review"}),
])
def test_http_claim_rejects_remote_proxy_origin_and_wrong_intent(http_case, peer, host, extra):
    from fastapi.testclient import TestClient
    case = http_case
    before = (case.directory / "job.json").read_bytes()
    client = TestClient(case.app, base_url="http://127.0.0.1:18765", client=(peer, 43202))
    client.get("/api/health")
    response = client.post(case.path, json={"token": case.token},
                           headers={**case.headers, "Host": host, "Origin": "http://" + host, **extra})
    assert response.status_code == 403
    assert before == (case.directory / "job.json").read_bytes()
    client.close()


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://127.0.0.1:18765"},
    {"X-Justbuild-Intent": "intake-claim"}])
def test_http_claim_requires_origin_and_intent_headers(http_case, headers):
    case = http_case
    assert case.client.post(case.path, json={"token": case.token}, headers=headers).status_code == 403
    assert case.app.state.manager.get(JOB)["owner_sha256"] is None


def test_http_claim_body_exact_and_query_token_refused(http_case):
    case = http_case
    assert case.client.post(case.path, json={"token": case.token, "owner_sha256": OTHER}, headers=case.headers).status_code == 400
    assert case.client.post(case.path, json={}, headers=case.headers).status_code == 400
    assert case.client.post(case.path + "?token=" + case.token, json={"token": case.token}, headers=case.headers).status_code == 403
    assert case.app.state.manager.get(JOB)["owner_sha256"] is None


def test_claimed_readonly_share_still_cannot_confirm_print_or_reclaim(http_case):
    from fastapi.testclient import TestClient
    case = http_case
    response = case.client.post(case.path, json={"token": case.token}, headers=case.headers)
    assert response.status_code == 200
    share_url = response.json()["share_url"]
    viewer = TestClient(case.app, base_url="http://127.0.0.1:18765", client=("127.0.0.1", 43203))
    viewer.get("/api/health")
    shared_token = parse_qs(urlparse(share_url).query)["view"][0]
    headers = {"X-Review-Token": shared_token, "Origin": "http://127.0.0.1:18765"}
    value = assert_public_claim_scrubbed(viewer.get("/api/jobs/" + JOB, headers=headers), case.token)
    assert value["can_edit"] is False
    assert viewer.post("/api/jobs/" + JOB + "/confirm", json={"constraints": {"confirmed": True}}, headers=headers).status_code == 404
    assert viewer.post("/api/jobs/" + JOB + "/print", json={}, headers=headers).status_code == 404
    assert viewer.post(case.path, json={"token": case.token}, headers={**headers, "X-Justbuild-Intent": "intake-claim"}).status_code == 409
    assert case.app.state.manager.get(JOB)["state"] == "awaiting_confirmation"
    viewer.close()
