"""Linux download/hash receiver; opening Studio is an explicit separate action.

No account store, printer API, shell, or remote-selected program is accessed.
"""
from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx


class ReceiverError(ValueError):
    """Non-sensitive error: messages never contain bearer URLs or response bodies."""


def _uri(value: str, *, origin_only: bool, allow_loopback_http: bool):
    if (not isinstance(value, str) or len(value) > 4096 or not value
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
            or any(c in value for c in ('\\', '"', '%'))):
        raise ReceiverError("Invalid trusted URL")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ReceiverError("Invalid trusted URL") from None
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or port == 0):
        raise ReceiverError("Invalid trusted URL")
    try:
        address = ipaddress.ip_address(host)
        rendered = f"[{host}]" if address.version == 6 else host
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                                     for p in host.split(".")):
            raise ReceiverError("Invalid trusted host") from None
        rendered = host
    if parsed.scheme != "https" and not (
            allow_loopback_http and parsed.scheme == "http" and host in ("127.0.0.1", "::1")):
        raise ReceiverError("HTTPS is required; HTTP debugging accepts only 127.0.0.1 or ::1")
    if origin_only and parsed.path not in ("", "/"):
        raise ReceiverError("Allowed origin must not contain a path")
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    origin = f"{parsed.scheme}://{rendered}" + (f":{port}" if port else "")
    return parsed, (parsed.scheme, host, effective_port), origin


def _json(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ReceiverError("Invalid manifest or local receipt") from None


def _read_response(client: httpx.Client, url: str, limit: int, sink=None) -> bytes | int:
    try:
        with client.stream("GET", url, follow_redirects=False,
                           headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code != 200:
                raise ReceiverError("The trusted service did not return HTTP 200; redirects are rejected")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ReceiverError("Encoded HTTP bodies are not accepted")
            declared = response.headers.get("content-length")
            if declared is not None and (not declared.isdigit() or int(declared) > limit):
                raise ReceiverError("Response exceeds the allowed byte count")
            output = bytearray() if sink is None else None
            total, deadline = 0, time.monotonic() + 120
            for chunk in response.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > limit or time.monotonic() > deadline:
                    raise ReceiverError("Response exceeds the allowed size or duration")
                if sink is None:
                    output.extend(chunk)
                else:
                    sink.write(chunk)
            return bytes(output) if sink is None else total
    except ReceiverError:
        raise
    except Exception:
        raise ReceiverError("File transfer failed; no printer action was requested") from None


def _open_dir(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    if os.fstat(fd).st_uid != os.getuid():
        os.close(fd)
        raise ReceiverError("Receiver cache directory has a different owner")
    return fd


def _read_local(directory: int, name: str, limit: int) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_size > limit:
            raise ReceiverError("Unsafe local cache file")
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    if (len(data) > limit or len(data) != before.st_size or
            (before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ReceiverError("Local cache file changed during validation")
    return data


def _write_receipt(directory: int, name: str, receipt: dict) -> None:
    temporary = "." + secrets.token_hex(16) + ".tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2).encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _desktop_env() -> dict:
    env = dict(os.environ)
    runtime = Path("/run/user") / str(os.getuid())
    if (runtime / "bus").exists() and runtime.stat().st_uid == os.getuid():
        env.setdefault("XDG_RUNTIME_DIR", str(runtime))
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(runtime / "bus"))
    return env


def receive(handoff_url: str, *, allowed_origin: str, cache_root: Path,
            open_studio: bool = False, launcher: Path | None = None,
            allow_loopback_http: bool = False, max_bytes: int = 128 * 1024 * 1024,
            client: httpx.Client | None = None) -> dict:
    """Receive one capability; default download-only. Return a token-free receipt.

    ``launcher`` is trusted local configuration, never a manifest field. The
    HTTP client injection is for controlled tests; no remote data selects code.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ReceiverError("Invalid file-size limit")
    parsed, origin_key, origin = _uri(handoff_url, origin_only=False,
                                    allow_loopback_http=allow_loopback_http)
    _, allowed_key, _ = _uri(allowed_origin, origin_only=True,
                             allow_loopback_http=allow_loopback_http)
    if origin_key != allowed_key or not re.fullmatch(r"/handoffs/[0-9a-f]{64}", parsed.path):
        raise ReceiverError("Handoff must be on the configured origin and use a valid capability path")
    if open_studio:
        if launcher is None or not launcher.is_absolute() or not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise ReceiverError("Opening Studio requires an explicitly configured executable launcher")
        launcher = launcher.resolve()
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False)
    descriptors, lock, temporary = [], None, None
    try:
        manifest = _json(_read_response(client, handoff_url + "/manifest", 65536))
        ident, sha, size, basename = (manifest.get(k) for k in ("id", "sha256", "bytes", "basename"))
        if (not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ident)
                or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha)
                or isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= max_bytes
                or not isinstance(basename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.gcode\.3mf", basename)):
            raise ReceiverError("Invalid file identity, hash, size or basename")
        sha = sha.lower()
        download = manifest.get("download_url")
        if not isinstance(download, str):
            raise ReceiverError("Invalid download URL")
        download = urljoin(handoff_url + "/manifest", download)
        file_uri, file_origin, _ = _uri(download, origin_only=False,
                                       allow_loopback_http=allow_loopback_http)
        if file_origin != origin_key or file_uri.path != parsed.path + "/file.gcode.3mf":
            raise ReceiverError("Download must name this capability on the configured origin")
        root = cache_root.expanduser().absolute()
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ReceiverError("Receiver cache must not traverse symbolic links")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptors.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        if os.fstat(descriptors[0]).st_uid != os.getuid():
            raise ReceiverError("Receiver cache has a different owner")
        descriptors.append(_open_dir(descriptors[0], "inbox"))
        descriptors.append(_open_dir(descriptors[1], ident))
        inbox_fd = descriptors[-1]
        descriptors.append(_open_dir(descriptors[0], "receipts"))
        receipt_fd = descriptors[-1]
        lock = os.open(ident + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=receipt_fd)
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ReceiverError("Unsafe receiver lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReceiverError("This handoff is already being received") from None
        destination = root / "inbox" / ident / basename
        receipt_name = ident + ".json"
        receipt = {"schema_version": "0.1", "id": ident, "origin": origin, "sha256": sha,
                   "bytes": size, "basename": basename, "local_path": str(destination),
                   "download_status": "pending", "open_status": "pending",
                   "studio_import": "not_observed", "printer_receipt": "not_observed",
                   "print_start": "not_observed"}
        previous = _read_local(receipt_fd, receipt_name, 65536)
        if previous is not None:
            previous = _json(previous)
            if any(previous.get(k) != receipt[k] for k in ("id", "origin", "sha256", "bytes", "basename", "local_path")):
                raise ReceiverError("Handoff conflicts with its existing local receipt")
            if previous.get("open_status") not in ("pending", "launch_unknown", "process_started"):
                raise ReceiverError("Unknown local launch state; reconcile before retrying")
            receipt = previous
        cached = _read_local(inbox_fd, basename, size)
        if cached is not None:
            if len(cached) != size or hashlib.sha256(cached).hexdigest() != sha:
                raise ReceiverError("Existing cached file failed integrity validation")
        else:
            if receipt["download_status"] == "verified":
                raise ReceiverError("Previously verified cache file is missing; reconcile before retrying")
            temporary = "." + secrets.token_hex(16) + ".partial"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=inbox_fd)
            with os.fdopen(fd, "wb") as handle:
                received = _read_response(client, download, size, handle)
                handle.flush()
                os.fsync(handle.fileno())
            downloaded = _read_local(inbox_fd, temporary, size)
            if received != size or len(downloaded) != size or hashlib.sha256(downloaded).hexdigest() != sha:
                raise ReceiverError("Downloaded file failed size or SHA-256 validation")
            os.replace(temporary, basename, src_dir_fd=inbox_fd, dst_dir_fd=inbox_fd)
            temporary = None
            os.fsync(inbox_fd)
        receipt["download_status"] = "verified"
        receipt["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_receipt(receipt_fd, receipt_name, receipt)
        if not open_studio or receipt["open_status"] in ("launch_unknown", "process_started"):
            return dict(receipt)
        receipt["open_status"] = "launch_unknown"
        _write_receipt(receipt_fd, receipt_name, receipt)
        try:
            process = subprocess.Popen([str(launcher), "--single-instance", str(destination)],
                                       shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, close_fds=True,
                                       start_new_session=True, env=_desktop_env())
        except Exception:
            raise ReceiverError("Studio launch outcome is unknown; inspect the local receipt before retrying") from None
        receipt["open_status"] = "process_started"
        receipt["process_id"] = process.pid
        _write_receipt(receipt_fd, receipt_name, receipt)
        return dict(receipt)
    except ReceiverError:
        raise
    except Exception:
        raise ReceiverError("Receiver could not complete; inspect local receipts before retrying") from None
    finally:
        if temporary is not None and len(descriptors) >= 3:
            try:
                os.unlink(temporary, dir_fd=descriptors[2])
            except FileNotFoundError:
                pass
        if lock is not None:
            os.close(lock)
        for fd in reversed(descriptors):
            os.close(fd)
        if own_client:
            client.close()
