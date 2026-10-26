"""One-time local ownership claims for explicitly marked, newly ingested jobs.

Only the trusted local intake CLI calls the issuer. No public issuance route is
provided. Raw claim tokens are returned once and must only be placed in a local
URL fragment; persist only their SHA-256. HTTP callers enforce loopback/Origin/
intent and strip every intake_claim* field from public job responses.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import time

_SHA = re.compile(r"[0-9a-f]{64}")
_SCOPE = "local_intake_owner"


class IntakeClaimError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str):
    raise IntakeClaimError(code, message)


def _sha(value, field: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        _fail("INTAKE_CLAIM_INPUT", field + " must be 64 lowercase hexadecimal characters")
    return value


def _eligible(manager, job_id: str) -> dict:
    try:
        job = manager.get(job_id)
    except (ValueError, FileNotFoundError):
        _fail("INTAKE_CLAIM_UNAVAILABLE", "This job is not eligible for a local intake claim")
    if (job.get("intake_claim_eligible") is not True or job.get("owner_sha256") is not None
            or job.get("state") not in ("awaiting_confirmation", "needs_input") or job.get("print_hold")):
        _fail("INTAKE_CLAIM_UNAVAILABLE", "This job is not eligible for a local intake claim")
    return job


def issue_intake_claim(manager, job_id: str, ttl_seconds: int = 3600) -> str:
    """Issue once for a trusted new-intake marker; never claim an existing job.

    The caller must set ``intake_claim_eligible=True`` only immediately after
    creating a new intake job. No geometry, review hold or print state changes.
    """
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
        _fail("INTAKE_CLAIM_TTL", "Claim lifetime must be an integer between 1 and 3600 seconds")
    with manager.lock:
        job = _eligible(manager, job_id)
        if "intake_claim" in job:
            _fail("INTAKE_CLAIM_ALREADY_ISSUED", "A claim has already been issued for this intake")
        token = secrets.token_hex(32)
        issued = time.time()
        manager.update(job_id, intake_claim={"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                       "issued_at": issued, "expires_at": issued + ttl_seconds, "scope": _SCOPE})
    return token


def claim_intake_job(manager, job_id: str, token: str, owner_sha256: str) -> dict:
    """Bind an unowned marked intake to one browser session, atomically once.

    Concurrent claims are serialized by the manager's RLock; owner binding,
    read-only sharing capability and claim consumption use one atomic update.
    No generated/sliced artifact or review/print hold is created or cleared.
    """
    token = _sha(token, "Claim token")
    owner_sha256 = _sha(owner_sha256, "Session owner")
    supplied = hashlib.sha256(token.encode()).hexdigest()
    with manager.lock:
        job = _eligible(manager, job_id)
        record = job.get("intake_claim")
        if (not isinstance(record, dict) or record.get("scope") != _SCOPE
                or not isinstance(record.get("token_sha256"), str)
                or _SHA.fullmatch(record["token_sha256"]) is None
                or not hmac.compare_digest(record["token_sha256"], supplied)):
            _fail("INTAKE_CLAIM_INVALID", "The one-time local intake claim is invalid")
        expires = record.get("expires_at")
        if (type(expires) not in (int, float) or not math.isfinite(expires)
                or time.time() >= expires):
            _fail("INTAKE_CLAIM_EXPIRED", "The local intake claim has expired; create a new intake")
        review_token = secrets.token_hex(24)
        return manager.update(job_id, owner_sha256=owner_sha256, review_token=review_token,
                              share_url=f"/?job={job_id}&view={review_token}", intake_claim=None,
                              intake_claim_eligible=False, intake_claimed_at=time.time())
