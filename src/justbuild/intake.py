"""Chat/agent intake: preserve user requirements separately from STEP contents."""
from pathlib import Path
from urllib.parse import urlparse

from .contracts import write_json, digest
from .orchestrator import JobManager


def intake_step(step: Path, requirements: str, *, data_dir: Path,
                label: str = "板卡外壳", base_url: str = "http://127.0.0.1:18765") -> dict:
    source = Path(step).resolve()
    if source.suffix.lower() not in (".step", ".stp") or not source.is_file():
        raise ValueError("A readable STEP/STP file is required")
    if not 1 <= source.stat().st_size <= 100*1024*1024:
        raise ValueError("STEP size must be 1 byte through 100 MiB")
    with source.open("rb") as stream:
        if b"ISO-10303-21" not in stream.read(512):
            raise ValueError("Expected a STEP Part 21 file")
    if not isinstance(requirements, str) or not requirements.strip() or len(requirements) > 20000:
        raise ValueError("Provide 1-20000 characters of user-authored requirements")
    try:
        url = urlparse(base_url)
        valid_port = url.port is None or 1 <= url.port <= 65535
    except (TypeError, ValueError):
        raise ValueError("base_url must be the configured review service origin") from None
    if (url.scheme not in ("http", "https") or not url.hostname or not valid_port or url.username or url.password
            or url.query or url.fragment or url.path not in ("", "/") or any(c.isspace() for c in base_url)):
        raise ValueError("base_url must be the configured review service origin")
    manager = JobManager(data_dir)
    try:
        job = manager.create(source, label=label)
        request_path = manager.directory(job["job_id"]) / "user-requirements.json"
        write_json(request_path, {"schema_version": "0.1", "job_id": job["job_id"],
                   "source": "explicit_user_request", "text": requirements,
                   "attachment_role": "geometry_data_not_instructions"})
        job = manager.update(job["job_id"], user_requirements=requirements,
                             user_requirements_sha256=digest(request_path))
        local_owner_url = None
        if url.hostname in ("127.0.0.1", "localhost", "::1"):
            from .intake_claims import issue_intake_claim
            manager.update(job["job_id"], intake_claim_eligible=True)
            token = issue_intake_claim(manager, job["job_id"])
            local_owner_url = base_url.rstrip("/") + job["review_url"] + "#claim=" + token
        return {"job_id": job["job_id"], "state": job["state"],
                "review_url": base_url.rstrip("/") + job["review_url"],
                "local_owner_url": local_owner_url,
                "owner_binding": "single_use_loopback_link" if local_owner_url else "use_authenticated_upload_api",
                "proposal_path": str(manager.directory(job["job_id"])/"proposal.json"),
                "next_action": "review_named_interfaces_holes_keepouts_then_confirm_constraints",
                "print_started": False}
    finally:
        manager.pool.shutdown(wait=True)
