"""Expiring, download-only handoff of a verified sliced file to a desktop client.

This module neither contacts a printer nor grants a print approval. HTTP callers
must authorize job ownership before calling ``create``; an omitted owner is only
for internal callers handing off an unowned, CLI-created job.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import stat
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote, urlsplit


class HandoffError(ValueError):
    """A deliberately non-sensitive error suitable for a generic HTTP 404/409."""


_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_JOB = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PERMISSIONS = ["download_sliced_3mf"]


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _origin(value: str, allow_loopback_http: bool) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise HandoffError("A valid public HTTPS origin is required")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value or "%" in value:
        raise HandoffError("A valid public HTTPS origin is required")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise HandoffError("A valid public HTTPS origin is required") from exc
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or parsed.scheme not in ("https", "http") or port == 0):
        raise HandoffError("A valid public HTTPS origin is required")
    try:
        address = ipaddress.ip_address(host)
        loopback = address.is_loopback
        rendered_host = f"[{host}]" if address.version == 6 else host
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                                     for p in host.split(".")):
            raise HandoffError("A valid public HTTPS origin is required")
        loopback = host == "localhost"
        rendered_host = host
    if parsed.scheme != "https" and not (allow_loopback_http and loopback):
        raise HandoffError("HTTPS is required; HTTP is restricted to explicit loopback debugging")
    return f"{parsed.scheme}://{rendered_host}" + (f":{port}" if port is not None else "")


def _relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise HandoffError("Invalid handoff artifact")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in (".", "..", "") for p in path.parts):
        raise HandoffError("Invalid handoff artifact")
    return path.parts


class HandoffStore:
    """Atomic disk tickets, retaining only token hashes and an authenticated seal.

    At most ``max_bytes`` are read into memory for a download. Returning validated
    bytes (rather than a filesystem path) prevents an HTTP FileResponse from
    reopening a different file after validation. No network calls occur here.
    """

    def __init__(self, root: Path, *, max_bytes: int = 128 * 1024 * 1024,
                 allow_loopback_http: bool = False, clock: Callable[[], float] = time.time):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.allow_loopback_http = allow_loopback_http
        self.clock = clock

    def create(self, manager, job_id: str, public_base_url: str, *,
               owner_sha256: str | None = None, ttl_seconds: int = 900) -> dict:
        """Create a read-only capability for exactly one reviewed sliced artifact.

        A supplied owner hash must match the stored owner. An omitted owner is
        accepted only for an unowned CLI job, so HTTP must separately require
        owner authorization and cannot use this branch for a share-link reader.
        """
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 3600:
            raise HandoffError("Handoff lifetime must be between 1 and 3600 seconds")
        origin = _origin(public_base_url, self.allow_loopback_http)
        if not isinstance(job_id, str) or not _JOB.fullmatch(job_id):
            raise HandoffError("Invalid job")
        job = manager.get(job_id)
        if job.get("print_hold"):
            raise HandoffError("This job is held for renewed review")
        owner = job.get("owner_sha256")
        if owner_sha256 is None:
            if owner is not None:
                raise HandoffError("Job ownership is required")
        elif (not isinstance(owner_sha256, str) or not _SHA.fullmatch(owner_sha256)
              or not isinstance(owner, str) or not hmac.compare_digest(owner_sha256, owner)):
            raise HandoffError("Job ownership is required")
        stages = job.get("stage_results", {})
        sliced = stages.get("slicing", {})
        result = sliced.get("result", {})
        if (job.get("job_id") != job_id or job.get("state") != "ready_to_print"
                or stages.get("verifying", {}).get("result", {}).get("verdict") != "pass"
                or result.get("status") != "ok" or result.get("simulation") is not False
                or result.get("job_id") != job_id):
            raise HandoffError("A verified, sliced job is required")
        candidates = [a for a in result.get("artifacts", []) if a.get("kind") == "sliced_3mf"]
        if len(candidates) != 1 or not _SHA.fullmatch(str(candidates[0].get("sha256", ""))):
            raise HandoffError("Exactly one hash-bound sliced file is required")
        item = candidates[0]
        declared = result.get("sliced_artifact", {})
        if any(declared.get(k) != item.get(k) for k in ("kind", "path", "sha256")):
            raise HandoffError("The prepared sliced artifact does not match its manifest")
        jobs_root = Path(manager.root).resolve()
        folder = jobs_root / job_id
        # Use lexical paths for traversal validation, then open each component
        # with O_NOFOLLOW. resolve() alone would hide links or permit races.
        stage_parts = _relative_parts(sliced.get("path", ""))
        raw = Path(item.get("path", ""))
        target = raw if raw.is_absolute() else folder.joinpath(*stage_parts).parent / raw
        try:
            relative = target.relative_to(folder).as_posix()
        except ValueError as exc:
            raise HandoffError("Invalid handoff artifact") from exc
        _relative_parts(relative)
        if not relative.lower().endswith(".gcode.3mf"):
            raise HandoffError("A sliced .gcode.3mf artifact is required")
        created = float(self.clock())
        if not math.isfinite(created):
            raise HandoffError("Invalid server clock")
        token = secrets.token_hex(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        record = {"schema_version": "0.1", "id": token_hash[:32], "token_sha256": token_hash,
                  "job_id": job_id, "owner_sha256": owner, "jobs_root": str(jobs_root),
                  "artifact_relative": relative, "sha256": item["sha256"], "origin": origin,
                  "created_epoch": created, "expires_epoch": created + ttl_seconds,
                  "basename": f"justbuild-{job_id[:12]}.gcode.3mf",
                  "label": str(job.get("label", "PCB enclosure"))[:120],
                  "synthetic": job.get("synthetic") if isinstance(job.get("synthetic"), bool) else None,
                  "printer_model": result.get("printer", {}).get("model"),
                  "settings": self._settings(result),
                  "permissions": list(_PERMISSIONS)}
        data = self._read_file(record)
        record["bytes"] = len(data)
        record["seal"] = hmac.new(bytes.fromhex(token), _canonical(record), hashlib.sha256).hexdigest()
        target_ticket = self.root / f"{token_hash}.json"
        temporary = self.root / f".{secrets.token_hex(16)}.tmp"
        try:
            with temporary.open("xb") as handle:
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass  # Some removable filesystems cannot enforce POSIX modes.
                handle.write(_canonical(record))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target_ticket)
        finally:
            temporary.unlink(missing_ok=True)
        return {**self._metadata(record, token), "token": token}

    def _load(self, token: str) -> dict:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise HandoffError("Handoff is unavailable")
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        path = self.root / f"{token_hash}.json"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise HandoffError("Handoff is unavailable")
                raw = handle.read(16385)
            if len(raw) > 16384:
                raise HandoffError("Handoff is unavailable")
            record = json.loads(raw)
            seal = record.pop("seal")
            expected = hmac.new(bytes.fromhex(token), _canonical(record), hashlib.sha256).hexdigest()
            if not isinstance(seal, str) or not hmac.compare_digest(seal, expected):
                raise HandoffError("Handoff is unavailable")
            record["seal"] = seal
            if (record.get("token_sha256") != token_hash or record.get("id") != token_hash[:32]
                    or record.get("schema_version") != "0.1" or record.get("permissions") != _PERMISSIONS
                    or not _JOB.fullmatch(record.get("job_id", ""))
                    or not _SHA.fullmatch(record.get("sha256", ""))):
                raise HandoffError("Handoff is unavailable")
            created, expires = record["created_epoch"], record["expires_epoch"]
            if (not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
                        for v in (created, expires)) or not 0 < expires - created <= 3600
                    or not created <= self.clock() < expires):
                raise HandoffError("Handoff is expired or unavailable")
            if _origin(record["origin"], self.allow_loopback_http) != record["origin"]:
                raise HandoffError("Handoff is unavailable")
            return record
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if isinstance(exc, HandoffError):
                raise
            raise HandoffError("Handoff is unavailable") from exc

    def _read_file(self, record: dict) -> bytes:
        descriptors = []
        try:
            jobs_root = Path(record["jobs_root"])
            if not jobs_root.is_absolute():
                raise HandoffError("Handoff artifact is unavailable")
            descriptors.append(os.open(jobs_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            parts = (record["job_id"],) + _relative_parts(record["artifact_relative"])
            for part in parts[:-1]:
                descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                           dir_fd=descriptors[-1]))
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1])
            with os.fdopen(fd, "rb") as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= self.max_bytes:
                    raise HandoffError("Handoff artifact is unavailable or exceeds the size limit")
                data = handle.read(self.max_bytes + 1)
                after = os.fstat(handle.fileno())
            if (len(data) > self.max_bytes or len(data) != before.st_size
                    or (before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_ino, after.st_size, after.st_mtime_ns)
                    or hashlib.sha256(data).hexdigest() != record["sha256"]
                    or ("bytes" in record and record["bytes"] != len(data))):
                raise HandoffError("Handoff artifact has changed")
            return data
        except (OSError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, HandoffError):
                raise
            raise HandoffError("Handoff artifact is unavailable") from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _metadata(self, record: dict, token: str) -> dict:
        landing = f"{record['origin']}/handoffs/{token}"
        download = landing + "/file.gcode.3mf"
        return {"id": record["id"], "job_id": record["job_id"], "label": record["label"],
                "basename": record["basename"], "sha256": record["sha256"],
                "bytes": record["bytes"], "size_bytes": record["bytes"],
                "created_at": _iso(record["created_epoch"]), "expires_at": _iso(record["expires_epoch"]),
                "permissions": list(_PERMISSIONS), "grants_print_permission": False,
                "synthetic": record["synthetic"], "printer_model": record["printer_model"],
                "settings": record["settings"],
                "landing_url": landing, "download_url": download, "manifest_url": landing + "/manifest",
                "receiver_uri": "justbuild://open?handoff=" + quote(landing, safe=""),
                "windows_uri": "bambustudio://open?file=" + quote(download, safe=""),
                "mac_uri": "bambustudioopen://" + download,
                "desktop_uri_dispatch_verified": False}

    @staticmethod
    def _settings(result: dict) -> dict:
        settings = result.get("settings", {})
        public = {k: settings[k] for k in ("nozzle_diameter_mm", "material", "bed_type")
                  if k in settings and isinstance(settings[k], (str, int, float))}
        public["profiles"] = {k: v["id"] for k, v in settings.get("profiles", {}).items()
                              if k in ("machine", "process", "filament")
                              and isinstance(v, dict) and isinstance(v.get("id"), str)}
        return public

    def get(self, token: str) -> dict:
        """Return public metadata only after TTL, ticket and file validation."""
        record = self._load(token)
        self._read_file(record)
        return self._metadata(record, token)

    def landing(self, token: str) -> dict:
        """Metadata for an HTML page; the HTTP renderer must escape text values."""
        return self.get(token)

    def payload(self, token: str) -> bytes:
        """Return exactly the authenticated sliced bytes, without a local path."""
        return self._read_file(self._load(token))
