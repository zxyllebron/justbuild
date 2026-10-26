"""Opt-in, loopback-only first pairing UI for the account-free A1 LAN adapter.

This module never uploads an artifact or submits a printer command. TLS discovery
is unauthenticated observation; saving pins requires explicit local confirmation.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .printing import lan

_COOKIE = "justbuild_session"
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", re.I)
_PIN = re.compile(r"[0-9a-f]{64}")
_PROXY_HEADERS = {"forwarded", "via", "x-real-ip", "x-original-host", "x-original-url", "x-rewrite-url"}
_PROFILE_KEYS = {"schema_version", "status", "host", "model", "serial_masked", "printer_id_sha256", "firmware",
                 "authorization_mode", "developer_mode_confirmed", "mqtt_pin_present", "ftp_pin_present",
                 "access_code_present", "dispatch_verified", "configured", "offline_ready", "ready_prerequisites", "network_check_performed"}


def _error(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def _private_host(value) -> str:
    if not isinstance(value, str):
        _error("LOCAL_PRINTER_HOST", "请输入打印机屏幕显示的私网 IPv4。")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        _error("LOCAL_PRINTER_HOST", "请输入打印机屏幕显示的私网 IPv4。")
    if not any(address in network for network in _PRIVATE_NETWORKS):
        _error("LOCAL_PRINTER_HOST", "仅支持 10/8、172.16/12、192.168/16 私网 IPv4。")
    return str(address)


def _guard(request: Request, enabled: bool, *, mutation: bool = False) -> str | None:
    if enabled is not True:
        raise HTTPException(404, "Not found")
    try:
        peer = ipaddress.ip_address(request.client.host) if request.client else None
    except ValueError:
        peer = None
    hosts = request.headers.getlist("host")
    authority = hosts[0] if len(hosts) == 1 else ""
    match = _HOST.fullmatch(authority)
    proxy = any(name in _PROXY_HEADERS or name.startswith("x-forwarded-") for name in request.headers)
    if (peer is None or not peer.is_loopback or match is None or proxy
            or (match.group(1) is not None and not 1 <= int(match.group(1)) <= 65535)
            or request.url.scheme not in ("http", "https")):
        _error("LOCAL_SETUP_ONLY", "此页仅允许通过本机回环地址直接访问。", 403)
    expected = request.url.scheme + "://" + authority.lower()
    origin = request.headers.get("origin")
    if (origin is not None and origin.lower() != expected) or request.headers.get("sec-fetch-site") == "cross-site":
        _error("LOCAL_SETUP_ORIGIN", "必须在本机配对页内操作。", 403)
    if not mutation:
        return None
    if (origin is None or request.headers.get("x-justbuild-intent") != "printer-setup"
            or request.query_params):
        _error("LOCAL_SETUP_INTENT", "请通过本机配对页提交操作。", 403)
    token = request.cookies.get(_COOKIE, "")
    owner = getattr(request.state, "owner", None)
    expected_owner = hashlib.sha256(token.encode()).hexdigest()
    if (not re.fullmatch(r"[0-9a-f]{64}", token) or not isinstance(owner, str)
            or not hmac.compare_digest(owner, expected_owner)):
        _error("LOCAL_SETUP_SESSION", "请刷新本机配对页以建立会话。", 403)
    return owner


async def _body(request: Request, fields: set[str]) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        _error("LOCAL_SETUP_JSON", "请使用 JSON 提交本机配对资料。", 415)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 4096:
            _error("LOCAL_SETUP_SIZE", "配对请求过大。", 413)
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError):
        _error("LOCAL_SETUP_JSON", "配对请求格式无效。")
    if not isinstance(value, dict) or set(value) != fields:
        _error("LOCAL_SETUP_FIELDS", "配对字段不完整或含有不支持的字段。")
    return value


def _public_profile(value: dict) -> dict:
    # Defence in depth: never serialize the private load_lan_profile object.
    result = {key: value[key] for key in _PROFILE_KEYS if key in value}
    result["dispatch_verified"] = False
    return result


def _lan_error(exc: lan.LanError) -> JSONResponse:
    code = exc.code if isinstance(exc.code, str) and re.fullmatch(r"LAN_[A-Z_]{1,64}", exc.code) else "LAN_SETUP_FAILED"
    return JSONResponse({"status": "needs_input", "code": code,
                         "message": "本地连接未完成，请核对打印机屏幕、访问码、固件和 Developer LAN 设置。",
                         "dispatch_verified": False, "print_command_sent": False}, status_code=400)


def _public_check(value: dict) -> dict:
    result = {key: value[key] for key in (
        "schema_version", "operation", "status", "account_required", "read_only", "observed_at",
        "mqtt_authenticated", "ftps_authenticated", "certificate_pins_verified",
        "connection_verified", "ready_for_new_print") if key in value}
    result.update(read_only=True, dispatch_verified=False, print_command_sent=False)
    if isinstance(value.get("profile"), dict):
        result["profile"] = _public_profile(value["profile"])
    if isinstance(value.get("printer"), dict):
        result["printer"] = {key: value["printer"][key] for key in (
            "identity_verified", "serial_matches", "a1_model_matches", "firmware_observed", "firmware_matches",
            "state", "version_reply_received", "fresh_status_received", "progress_percent", "observed_at")
            if key in value["printer"]}
    result["issues"] = [{"code": item.get("code") if re.fullmatch(r"LAN_[A-Z_]{1,64}", str(item.get("code", "")))
                         else "LAN_CONNECTION_FAILED", "message": "设备当前有任务，连接正常；不会提交新打印。"
                         if item.get("code") == "LAN_NOT_IDLE" and result.get("connection_verified") else "只读连接检查未通过，请核对配对资料与设备状态。"}
                        for item in value.get("issues", []) if isinstance(item, dict)]
    return result


def install_local_printer_routes(app: FastAPI, *, enabled: bool = False,
                                 profile_path: Path | None = None) -> None:
    """Register guarded routes. Main app must install its session middleware.

    ``enabled`` must be the literal boolean True. The installer does not enable
    itself from environment variables, configure a proxy, or change printer mode.
    A deployment may explicitly pass ``os.getenv('JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP') == '1'``.
    ``profile_path`` is trusted server configuration, never a client request field.
    """
    chosen_path = Path(profile_path) if profile_path is not None else None
    web = Path(__file__).parent / "web"
    probes: dict[str, dict] = {}
    lock = threading.Lock()
    active: set[str] = set()

    def prune():
        now = time.monotonic()
        for key in list(probes):
            if probes[key]["expires_at"] <= now:
                del probes[key]

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        if request.url.path in {"/static/printer-setup.html", "/static/printer-setup.js", "/static/printer-setup.css"}:
            return JSONResponse({"detail": "Not found"}, status_code=404, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if request.url.path == "/printer-setup" or request.url.path.startswith(("/printer-setup.", "/api/printers/local")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            response.headers["Vary"] = "Origin, Cookie"
        return response

    @app.get("/printer-setup", include_in_schema=False)
    async def setup_page(request: Request):
        _guard(request, enabled)
        return FileResponse(web / "printer-setup.html", media_type="text/html")

    @app.get("/printer-setup.js", include_in_schema=False)
    async def setup_js(request: Request):
        _guard(request, enabled)
        return FileResponse(web / "printer-setup.js", media_type="text/javascript")

    @app.get("/printer-setup.css", include_in_schema=False)
    async def setup_css(request: Request):
        _guard(request, enabled)
        return FileResponse(web / "printer-setup.css", media_type="text/css")

    @app.get("/api/printers/local", include_in_schema=False)
    async def local_status(request: Request):
        _guard(request, enabled)
        try:
            value = await run_in_threadpool(lan.lan_profile_status, path=chosen_path)
            return {"schema_version": "0.1", "profile": _public_profile(value),
                    "dispatch_verified": False, "print_command_sent": False}
        except lan.LanError as exc:
            if exc.code == "LAN_PROFILE_UNAVAILABLE":
                return {"schema_version": "0.1", "profile": {"status": "not_configured", "dispatch_verified": False},
                        "dispatch_verified": False, "print_command_sent": False}
            return _lan_error(exc)
        except Exception:
            return JSONResponse({"status": "needs_input", "code": "LAN_STATUS_FAILED",
                                 "message": "无法读取本机私有配置。", "dispatch_verified": False}, status_code=503)

    @app.post("/api/printers/local/probe", include_in_schema=False)
    async def local_probe(request: Request):
        owner = _guard(request, enabled, mutation=True)
        value = await _body(request, {"host", "developer_mode_confirmed"})
        host = _private_host(value["host"])
        if value["developer_mode_confirmed"] is not True:
            _error("LAN_MODE_CONFIRMATION", "请先在打印机上确认 Developer LAN 已开启；本程序不会更改模式。")
        with lock:
            prune()
            if owner in active or len(active) >= 4 or len(probes) >= 128:
                _error("LOCAL_PROBE_BUSY", "已有探测正在执行，请稍后重试。", 429)
            active.add(owner)
        try:
            observed = await run_in_threadpool(lan.probe_lan_tls, host, mqtt_port=8883, ftp_port=990)
            pins = {}
            for name, port in (("mqtt", 8883), ("ftp", 990)):
                item = observed.get("services", {}).get(name, {})
                pin = item.get("certificate_sha256", "")
                if (item.get("status") != "observed" or item.get("port") != port
                        or not isinstance(pin, str) or _PIN.fullmatch(pin.lower()) is None):
                    _error("LAN_PROBE_INCOMPLETE", "未读取到两个 TLS 服务的证书，请确认 IP、热点网络与 Developer LAN。", 409)
                pins[name + "_certificate_sha256"] = pin.lower()
            token = secrets.token_urlsafe(32)
            with lock:
                prune()
                # A new probe invalidates previous observations for this session.
                for key in list(probes):
                    if probes[key]["owner"] == owner:
                        del probes[key]
                probes[token] = {"owner": owner, "host": host, "pins": pins,
                                 "expires_at": time.monotonic() + 300, "pairing": False}
            return {"schema_version": "0.1", "status": "needs_confirmation", "probe_id": token,
                    "host": host, "expires_in_seconds": 300, **pins, "identity_verified": False,
                    "credentials_sent": False, "print_command_sent": False, "dispatch_verified": False}
        except lan.LanError as exc:
            return _lan_error(exc)
        except HTTPException:
            raise
        except Exception:
            return JSONResponse({"status": "needs_input", "code": "LAN_PROBE_FAILED",
                                 "message": "TLS 探测失败，未发送凭据或打印命令。"}, status_code=502)
        finally:
            with lock:
                active.discard(owner)

    @app.post("/api/printers/local/pair", include_in_schema=False)
    async def local_pair(request: Request):
        owner = _guard(request, enabled, mutation=True)
        value = await _body(request, {"probe_id", "host", "mqtt_certificate_sha256", "ftp_certificate_sha256",
                                     "serial", "access_code", "firmware", "developer_mode_confirmed", "fingerprint_confirmed"})
        host = _private_host(value["host"])
        if value["developer_mode_confirmed"] is not True or value["fingerprint_confirmed"] is not True:
            _error("LAN_PAIR_CONFIRMATION", "必须明确确认设备、Developer LAN 与本次证书固定。")
        if (not isinstance(value["serial"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", value["serial"])
                or not isinstance(value["access_code"], str) or not re.fullmatch(r"[A-Za-z0-9]{8}", value["access_code"])
                or not isinstance(value["firmware"], str) or not re.fullmatch(r"[!-~]{1,64}", value["firmware"])):
            _error("LAN_PAIR_FIELDS", "请填写屏幕上的序列号、8 位访问码与固件版本。")
        probe_id = value["probe_id"]
        if not isinstance(probe_id, str) or len(probe_id) > 128:
            _error("LAN_PROBE_REQUIRED", "请重新探测并确认当前证书。", 409)
        with lock:
            prune()
            record = probes.get(probe_id)
            if (record is None or record["owner"] != owner or record["host"] != host or record["pairing"]
                    or any(not isinstance(value[key], str) or _PIN.fullmatch(value[key]) is None
                           or not hmac.compare_digest(value[key], pin)
                           for key, pin in record["pins"].items())):
                _error("LAN_PROBE_REQUIRED", "探测已过期、已使用或与当前会话、IP、证书不符，请重新探测。", 409)
            record["pairing"] = True
        try:
            profile = {key: value[key] for key in ("host", "serial", "access_code", "firmware", "developer_mode_confirmed")}
            profile.update(record["pins"], mqtt_port=8883, ftp_port=990, authorization_mode="developer_lan")
            result = await run_in_threadpool(lan.save_lan_profile, profile, path=chosen_path)
            with lock:
                probes.pop(probe_id, None)
            return {"schema_version": "0.1", "status": "paired", "profile": _public_profile(result),
                    "print_command_sent": False, "dispatch_verified": False}
        except lan.LanError as exc:
            return _lan_error(exc)
        except Exception:
            return JSONResponse({"status": "needs_input", "code": "LAN_PAIR_FAILED",
                                 "message": "无法保存本机私有配置。", "dispatch_verified": False}, status_code=503)
        finally:
            with lock:
                if probe_id in probes:
                    probes[probe_id]["pairing"] = False

    @app.post("/api/printers/local/check", include_in_schema=False)
    async def local_check(request: Request):
        _guard(request, enabled, mutation=True)
        await _body(request, set())
        try:
            value = await run_in_threadpool(lan.check_lan_connection, profile_path=chosen_path)
            return _public_check(value)
        except lan.LanError as exc:
            return _lan_error(exc)
        except Exception:
            return JSONResponse({"status": "needs_input", "code": "LAN_CONNECTION_FAILED",
                                 "message": "只读连接检查失败，未上传文件或启动打印。",
                                 "read_only": True, "print_command_sent": False, "dispatch_verified": False}, status_code=502)
