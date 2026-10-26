"""Account-free A1 Developer-LAN prototype: pinned TLS, pairing and read-only checks.

The open MQTT/FTP compatibility protocol has no manufacturer stability promise.
Unknown firmware/mode never enables submission. No Studio session is accessed.
"""
from __future__ import annotations

import ftplib
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import ssl
import stat
import threading
import time

from .common import PrintError, now


class LanError(PrintError):
    def __init__(self, code: str, message: str, status: str = "needs_input"):
        super().__init__(code, message, status)


def profile_path() -> Path:
    return Path.home() / ".config/justbuild/printers/a1.json"


def _host(value) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except (ValueError, TypeError):
        raise LanError("LAN_HOST", "The printer host must be an IP address shown on its screen.") from None


def _port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise LanError("LAN_PORT", "Invalid printer service port.")
    return value


def _timeout(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 <= value <= 300:
        raise LanError("LAN_TIMEOUT", "Timeout must be between 0.1 and 300 seconds.")
    return float(value)


def _context() -> ssl.SSLContext:
    # Self-signed LAN certificates are authenticated by exact SHA-256 pins
    # BEFORE sending credentials. Discovery does not authenticate a device.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _pin(sock, expected: str) -> None:
    cert = sock.getpeercert(binary_form=True)
    if not cert or not hmac.compare_digest(hashlib.sha256(cert).hexdigest(), expected):
        raise LanError("LAN_CERTIFICATE_CHANGED", "Printer TLS certificate differs from the explicitly paired fingerprint.")


def probe_lan_tls(host: str, *, mqtt_port: int = 8883, ftp_port: int = 990,
                  timeout: float = 5.0) -> dict:
    """Uncredentialed discovery only; never save or trust the returned pins."""
    host, timeout = _host(host), _timeout(timeout)
    result = {"schema_version": "0.1", "operation": "lan_tls_probe", "host": host,
              "trusted": False, "credentials_sent": False, "printer_command_sent": False,
              "observed_at": now(), "services": {}}
    for name, port in (("mqtt", _port(mqtt_port)), ("ftp", _port(ftp_port))):
        try:
            with socket.create_connection((host, port), timeout=timeout) as raw:
                with _context().wrap_socket(raw, server_hostname=host) as connection:
                    cert = connection.getpeercert(binary_form=True)
                    result["services"][name] = {"status": "observed", "port": port,
                        "certificate_sha256": hashlib.sha256(cert).hexdigest(),
                        "tls_version": connection.version(), "identity_verified": False}
        except Exception:
            result["services"][name] = {"status": "unavailable", "port": port}
    result["status"] = "needs_input"
    result["next_step"] = "Compare printer IP/serial and fingerprints through a trusted local pairing step; discovery alone grants no access."
    return result


def _private_write(path: Path, value: dict) -> None:
    path = path.expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise LanError("LAN_PRIVATE_PATH", "Private configuration must not traverse symbolic links.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.parent / (".lan-" + secrets.token_hex(16))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _profile(values: dict) -> dict:
    if not isinstance(values, dict):
        raise LanError("LAN_PROFILE", "A private LAN connection profile is required.")
    result = {"schema_version": "0.1", "transport": "lan", "model": "Bambu Lab A1",
              "host": _host(values.get("host")), "mqtt_port": _port(values.get("mqtt_port", 8883)),
              "ftp_port": _port(values.get("ftp_port", 990))}
    serial, code = values.get("serial"), values.get("access_code")
    if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", serial):
        raise LanError("LAN_SERIAL", "Enter the printer serial shown on its own screen.")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{8}", code):
        raise LanError("LAN_ACCESS_CODE", "A hidden eight-character local access code is required.")
    result.update(serial=serial, access_code=code)
    for name in ("mqtt_certificate_sha256", "ftp_certificate_sha256"):
        value = values.get(name, "")
        if not isinstance(value, str) or (value and not re.fullmatch(r"[0-9a-fA-F]{64}", value)):
            raise LanError("LAN_PIN", "A paired SHA-256 certificate fingerprint must contain 64 hexadecimal characters.")
        result[name] = value.lower()
    firmware = values.get("firmware", "")
    if not isinstance(firmware, str) or len(firmware) > 64 or any(ord(c) < 33 or ord(c) > 126 for c in firmware):
        raise LanError("LAN_FIRMWARE", "Invalid firmware identifier.")
    result["firmware"] = firmware
    mode = values.get("authorization_mode", "unknown")
    if mode not in ("unknown", "developer_lan"):
        raise LanError("LAN_MODE", "Only explicitly confirmed Developer LAN is implemented; standard authorized mode needs an official adapter.")
    result["authorization_mode"] = mode
    result["developer_mode_confirmed"] = values.get("developer_mode_confirmed") is True
    return result


def _public(profile: dict) -> dict:
    ready = bool(profile["firmware"] and profile["authorization_mode"] == "developer_lan"
                 and profile["developer_mode_confirmed"]
                 and profile["mqtt_certificate_sha256"] and profile["ftp_certificate_sha256"])
    return {"schema_version": "0.1", "status": "configured" if ready else "needs_input",
            "host": profile["host"], "model": "Bambu Lab A1", "serial_masked": "***" + profile["serial"][-4:],
            "printer_id_sha256": hashlib.sha256(profile["serial"].encode()).hexdigest(),
            "firmware": profile["firmware"], "authorization_mode": profile["authorization_mode"],
            "developer_mode_confirmed": profile["developer_mode_confirmed"],
            "mqtt_pin_present": bool(profile["mqtt_certificate_sha256"]),
            "ftp_pin_present": bool(profile["ftp_certificate_sha256"]),
            "access_code_present": True, "dispatch_verified": False}


def save_lan_profile(values: dict, *, path: Path | None = None) -> dict:
    """Store locally, atomically, 0600. Never return the access code or full serial."""
    profile = _profile(values)
    _private_write(path or profile_path(), profile)
    return _public(profile)


def _profile_bytes(path: Path | None = None) -> bytes:
    """Read one bounded private snapshot without following a final symlink."""
    try:
        chosen = (path or profile_path()).expanduser().absolute()
        if any(p.is_symlink() for p in (chosen, *chosen.parents)):
            raise OSError()
        descriptor = os.open(chosen, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise OSError()
            data = handle.read(16385)
        if len(data) > 16384:
            raise OSError()
        return data
    except LanError:
        raise
    except Exception:
        raise LanError("LAN_PROFILE_UNAVAILABLE", "A current-user private LAN profile with mode 0600 is required.") from None


def load_lan_profile(path: Path | None = None, *, expected_sha256: str | None = None) -> dict:
    """Private snapshot; an optional reviewed hash binds all connection settings.

    Both reads must match the review. The parsed dictionary comes from the
    first validated bytes, so subsequent filesystem changes cannot change the
    credentials/host/pins used by an in-flight operation.
    """
    if expected_sha256 is not None and (not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
        raise LanError("LAN_PROFILE_DIGEST", "A reviewed SHA-256 pairing identity is required.")
    data = _profile_bytes(path)
    if expected_sha256 is not None and not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_sha256):
        raise LanError("LAN_PROFILE_CHANGED", "The paired connection settings changed after review.")
    try:
        profile = _profile(json.loads(data))
    except LanError:
        raise
    except Exception:
        raise LanError("LAN_PROFILE_UNAVAILABLE", "A current-user private LAN profile with mode 0600 is required.") from None
    if expected_sha256 is not None and not hmac.compare_digest(hashlib.sha256(_profile_bytes(path)).hexdigest(), expected_sha256):
        raise LanError("LAN_PROFILE_CHANGED", "The paired connection settings changed during review validation.")
    return profile


def lan_profile_status(*, path: Path | None = None) -> dict:
    """Local-only metadata. Never connect, return a code, or expose full SN."""
    import importlib.util
    try:
        profile = load_lan_profile(path)
        public = _public(profile)
        try:
            mqtt_available = importlib.util.find_spec("paho.mqtt.client") is not None
        except ModuleNotFoundError:
            mqtt_available = False
        public.update({"configured": True, "network_check_performed": False,
            "ready_prerequisites": {"firmware_present": bool(profile["firmware"]),
                "developer_lan_user_confirmed": profile["authorization_mode"] == "developer_lan" and profile["developer_mode_confirmed"],
                "mqtt_pin_present": bool(profile["mqtt_certificate_sha256"]),
                "ftp_pin_present": bool(profile["ftp_certificate_sha256"]),
                "mqtt_dependency_available": mqtt_available}})
        public["offline_ready"] = all(public["ready_prerequisites"].values())
        return public
    except LanError as exc:
        return {"schema_version": "0.1", "configured": False, "offline_ready": False,
                "status": "needs_input", "network_check_performed": False,
                "dispatch_verified": False, "ready_prerequisites": {},
                "issues": [{"code": exc.code, "message": str(exc)}]}


class PinnedFTP(ftplib.FTP_TLS):
    """Implicit TLS on 990, pinned before USER/PASS and on every data channel."""
    def __init__(self, profile: dict, timeout: float):
        self.profile = profile
        super().__init__(context=_context(), timeout=timeout)

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        self.host, self.port = self.profile["host"], self.profile["ftp_port"]
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
            _pin(self.sock, self.profile["ftp_certificate_sha256"])
            self.af = self.sock.family
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
            return self.welcome
        except Exception:
            raw.close()
            self.close()
            raise

    def ntransfercmd(self, cmd, rest=None):
        connection, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        try:
            if not self._prot_p:
                raise LanError("LAN_FTPS_REQUIRED", "Encrypted FTPS data channels are required.")
            connection = self.context.wrap_socket(connection, server_hostname=self.host, session=self.sock.session)
            _pin(connection, self.profile["ftp_certificate_sha256"])
            return connection, size
        except Exception:
            connection.close()
            raise

    def __enter__(self):
        try:
            self.connect()
            self.login("bblp", self.profile["access_code"])
            self.prot_p()
            return self
        except Exception:
            self.close()
            raise


class LanMQTT:
    """Bounded single connection. No reconnection or automatic command retry."""
    def __init__(self, profile: dict, timeout: float):
        self.profile, self.timeout = profile, timeout
        self.connected, self.subscribed = threading.Event(), threading.Event()
        self.messages = queue.Queue(maxsize=200)
        self.pin_checked = False
        self.failed = False
        self.client = None

    def __enter__(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise LanError("LAN_MQTT_DEPENDENCY", "Install the optional paho-mqtt 2.x dependency before checking LAN connectivity.") from None
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="justbuild-" + secrets.token_hex(8),
                                  clean_session=True, reconnect_on_failure=False)
        self.client.username_pw_set("bblp", self.profile["access_code"])
        self.client.tls_set_context(_context())
        # Hostname verification is replaced by an exact user-paired certificate
        # hash. on_socket_open runs after TLS and BEFORE the MQTT CONNECT packet.
        self.client.tls_insecure_set(True)

        def opened(_client, _userdata, sock):
            _pin(sock, self.profile["mqtt_certificate_sha256"])
            self.pin_checked = True

        def connected(client, _userdata, _flags, reason, _properties):
            self.failed = bool(getattr(reason, "is_failure", reason != 0))
            self.connected.set()
            if not self.failed:
                result, _ = client.subscribe("device/" + self.profile["serial"] + "/report", qos=1)
                self.failed = result != 0

        def subscribed(_client, _userdata, _mid, reasons, _properties):
            self.failed = self.failed or any(getattr(r, "is_failure", False) for r in reasons)
            self.subscribed.set()

        def message(_client, _userdata, message):
            if message.topic != "device/" + self.profile["serial"] + "/report" or len(message.payload) > 65536:
                return
            try:
                value = json.loads(message.payload)
                if not isinstance(value, dict):
                    return
                self.messages.put_nowait((time.monotonic(), message.retain, value))
            except (ValueError, queue.Full):
                pass

        self.client.on_socket_open = opened
        self.client.on_connect = connected
        self.client.on_subscribe = subscribed
        self.client.on_message = message
        try:
            self.client.connect(self.profile["host"], self.profile["mqtt_port"], keepalive=30)
            if not self.pin_checked:
                raise LanError("LAN_MQTT_PIN", "MQTT credentials were not authorized for an unpinned socket.")
            self.client.loop_start()
            if not self.connected.wait(self.timeout) or not self.subscribed.wait(self.timeout) or self.failed:
                raise LanError("LAN_MQTT_AUTH", "Printer MQTT login or report subscription was not acknowledged.")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def publish(self, value: dict, *, qos: int = 1) -> dict:
        # Query compatibility is explicit. Never downgrade or retry a print
        # publication automatically when its QoS 1 acknowledgement is unknown.
        if type(qos) is not int or qos not in (0, 1):
            raise LanError("LAN_MQTT_QOS", "Only explicit MQTT QoS 0 or 1 is supported.")
        result = self.client.publish("device/" + self.profile["serial"] + "/request",
                                     json.dumps(value, separators=(",", ":")), qos=qos, retain=False)
        result.wait_for_publish(timeout=self.timeout)
        if result.rc != 0 or not result.is_published():
            if qos == 0:
                raise LanError("LAN_MQTT_LOCAL_SEND", "Local MQTT socket write completion is unknown.")
            raise LanError("LAN_MQTT_PUBACK", "MQTT broker publication acknowledgement is unknown.")
        return {"qos": qos, "stage": "local_send_completed" if qos == 0 else "mqtt_broker_puback",
                "broker_publication_acknowledged": qos == 1, "establishes_printer_receipt": False}

    def read(self, timeout: float):
        try:
            return self.messages.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return None

    def __exit__(self, *_args):
        if self.client is not None:
            try:
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:
                pass


def _ams_slot0(status: dict) -> dict:
    """Expose only the fixed AMS unit/slot material, never RFID or spool IDs."""
    def bit_set(value):
        if type(value) is int:
            return value >= 0 and bool(value & 1)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{1,8}", value):
            return bool(int(value, 16) & 1)
        return False

    ams = status.get("ams", {})
    if not isinstance(ams, dict):
        ams = {}
    present = bit_set(ams.get("ams_exist_bits")) and bit_set(ams.get("tray_exist_bits"))
    units = ams.get("ams", [])
    units = units if isinstance(units, list) else []
    matching = [unit for unit in units if isinstance(unit, dict) and str(unit.get("id")) == "0"]
    trays = matching[0].get("tray", []) if len(matching) == 1 else []
    trays = trays if isinstance(trays, list) else []
    slots = [tray for tray in trays if isinstance(tray, dict) and str(tray.get("id")) == "0"]
    material = slots[0].get("tray_type") if len(slots) == 1 else None
    return {"present": bool(present and len(slots) == 1),
            "material": material if material == "PLA" else "unknown"}


def _single_pla_3mf(data: bytes) -> None:
    """AMS slot 0 is implemented only for one explicitly declared PLA filament."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            settings_name, slice_name = "Metadata/project_settings.config", "Metadata/slice_info.config"
            if any(archive.namelist().count(name) != 1 or archive.getinfo(name).file_size > 1024 * 1024
                   for name in (settings_name, slice_name)):
                raise ValueError()
            settings = json.loads(archive.read(settings_name))
            raw_slice = archive.read(slice_name)
            if b"<!DOCTYPE" in raw_slice.upper() or b"<!ENTITY" in raw_slice.upper():
                raise ValueError()
            root = ET.fromstring(raw_slice)
            plates = root.findall("plate")
            if len(plates) != 1 or settings.get("filament_type") != ["PLA"]:
                raise ValueError()
            filaments = plates[0].findall("filament")
            if len(filaments) != 1 or filaments[0].get("id") != "1" or filaments[0].get("type") != "PLA":
                raise ValueError()
            metadata = {node.get("key"): node.get("value") for node in plates[0].findall("metadata")}
            if metadata.get("filament_maps") != "1":
                raise ValueError()
            if any(node.get("filament_list") != "0" for node in plates[0].findall("layer_filament_lists/layer_filament_list")):
                raise ValueError()
    except Exception:
        raise LanError("LAN_AMS_SINGLE_PLA", "AMS slot 0 requires a single PLA filament in both sliced project and plate metadata.") from None


def _read_status(mqtt: LanMQTT, profile: dict, timeout: float) -> dict:
    sequence = str(secrets.randbelow(2_000_000_000) + 1)
    start = time.monotonic()
    publications = [mqtt.publish({"info": {"sequence_id": sequence, "command": "get_version"}}, qos=0),
        mqtt.publish({"pushing": {"sequence_id": sequence, "command": "pushall", "version": 1, "push_target": 1}}, qos=0)]
    versions, status = None, None
    while time.monotonic() - start < timeout:
        item = mqtt.read(timeout - (time.monotonic() - start))
        if item is None:
            break
        received, retained, message = item
        if retained or received < start:
            continue
        info = message.get("info", {})
        if (isinstance(info, dict) and info.get("command") == "get_version"
                and str(info.get("sequence_id")) == sequence and isinstance(info.get("module"), list)):
            versions = info["module"]
        state = message.get("print", {})
        if isinstance(state, dict) and state.get("gcode_state"):
            status = state
        if versions is not None and status is not None:
            break
    modules = [v for v in versions or [] if isinstance(v, dict)]
    ota = next((v for v in modules if v.get("name") == "ota"), {})
    firmware = ota.get("sw_ver") if isinstance(ota.get("sw_ver"), str) else None
    serial_matches = any(v.get("sn") == profile["serial"] for v in modules)
    a1_model = any(v.get("hw_ver") == "N2S" or v.get("product_name") in ("A1", "Bambu Lab A1") for v in modules)
    markers = []
    for field in ("subtask_name", "gcode_file"):
        value = (status or {}).get(field)
        if isinstance(value, str) and len(value) <= 512:
            # Public preflight need not disclose somebody's previous task name.
            for marker in (value, value.rsplit("/", 1)[-1]):
                markers.append(hashlib.sha256(marker.encode()).hexdigest())
    progress = (status or {}).get("mc_percent")
    if isinstance(progress, bool) or not isinstance(progress, (float, int)) or not 0 <= progress <= 100:
        progress = None
    return {"identity_verified": serial_matches and a1_model, "serial_matches": serial_matches,
            "a1_model_matches": a1_model, "firmware_observed": firmware,
            "firmware_matches": firmware == profile["firmware"] and bool(firmware),
            "state": str((status or {}).get("gcode_state", "UNKNOWN")),
            "version_reply_received": versions is not None,
            "fresh_status_received": status is not None,
            "query_publications": publications,
            "task_marker_sha256": sorted(set(markers)), "progress_percent": progress,
            "ams_slot0": _ams_slot0(status or {}),
            "observed_at": now()}


def check_lan_connection(*, profile_path: Path | None = None, timeout: float = 15.0) -> dict:
    """Read-only authenticated TLS/MQTT info and FTPS login/NOOP; no uploads."""
    result = {"schema_version": "0.1", "operation": "lan_connection_check", "status": "needs_input",
              "account_required": False, "read_only": True, "print_command_sent": False,
              "connection_verified": False, "ready_for_new_print": False,
              "dispatch_verified": False, "observed_at": now(), "issues": []}
    try:
        profile = load_lan_profile(profile_path)
        result["profile"] = _public(profile)
        timeout = _timeout(timeout)
        if result["profile"]["status"] != "configured":
            raise LanError("LAN_PAIRING_INCOMPLETE", "Confirm firmware, Developer LAN mode and both certificate fingerprints on the local pairing page.")
        with LanMQTT(profile, timeout) as mqtt:
            result["printer"] = _read_status(mqtt, profile, timeout)
        with PinnedFTP(profile, timeout) as ftp:
            ftp.voidcmd("NOOP")
        result["mqtt_authenticated"] = result["ftps_authenticated"] = True
        result["certificate_pins_verified"] = True
        printer = result["printer"]
        if not printer["identity_verified"] or not printer["firmware_matches"]:
            raise LanError("LAN_IDENTITY_OR_FIRMWARE", "Fresh printer identity or firmware did not match the paired A1 profile.")
        result["connection_verified"] = True
        if not printer["fresh_status_received"] or printer["state"] not in ("IDLE", "FINISH"):
            raise LanError("LAN_NOT_IDLE", "A fresh idle/finished printer status is required before a new print can be offered.")
        result["ready_for_new_print"] = True
        result["status"] = "ok"
        result["authorization_evidence"] = "user-confirmed Developer LAN setting; actuation capability not tested"
    except LanError as exc:
        result["issues"].append({"code": exc.code, "message": str(exc)})
    except Exception:
        result["issues"].append({"code": "LAN_CONNECTION_FAILED", "message": "Authenticated LAN preflight failed; no file or print command was sent."})
    return result


def _print_options(value: dict | None) -> dict:
    if not isinstance(value, dict):
        raise LanError("LAN_REVIEWED_OPTIONS", "LAN printing requires explicit options bound to the reviewed print job.")
    expected = {"bed_leveling", "flow_calibration", "vibration_calibration", "timelapse", "use_ams", "bed_type"}
    if value.get("use_ams") is True:
        expected = expected | {"ams_mapping"}
    if set(value) != expected or any(type(value[k]) is not bool for k in expected - {"bed_type", "ams_mapping"}):
        raise LanError("LAN_PRINT_OPTIONS", "Only the fixed, reviewed print-option fields are accepted.")
    if value["bed_type"] != "textured_plate":
        raise LanError("LAN_INITIAL_SCOPE", "LAN transport requires the reviewed textured PEI plate.")
    if value["use_ams"]:
        mapping = value["ams_mapping"]
        if type(mapping) is not list or len(mapping) != 1 or type(mapping[0]) is not int or mapping[0] != 0:
            raise LanError("LAN_AMS_MAPPING", "Only one material explicitly mapped to AMS unit 0 slot 0 is supported.")
        return {**value, "ams_mapping": [0]}
    return dict(value)


def validate_lan_print_options(value: dict | None) -> dict:
    """Validate a fixed review-manifest option set, returning an independent copy."""
    return _print_options(value)


def project_file_request(*, remote_file: str, sequence: str, md5: str, options: dict) -> dict:
    """Developer-LAN compatibility message; never arbitrary G-code or a URL."""
    options = _print_options(options)
    if (not re.fullmatch(r"jb_[0-9a-f]{24}\.gcode\.3mf", remote_file)
            or not re.fullmatch(r"[0-9]{1,10}", sequence) or not re.fullmatch(r"[0-9a-f]{32}", md5)):
        raise LanError("LAN_PROJECT_IDENTITY", "Invalid reviewed LAN artifact identity.")
    request = {"print": {"sequence_id": sequence, "command": "project_file",
        "param": "Metadata/plate_1.gcode", "project_id": "0", "profile_id": "0",
        "task_id": "0", "subtask_id": "0", "subtask_name": remote_file.removesuffix(".gcode.3mf"),
        "url": "ftp:///" + remote_file, "md5": md5,
        "bed_type": options["bed_type"], "bed_levelling": options["bed_leveling"],
        "flow_cali": options["flow_calibration"], "vibration_cali": options["vibration_calibration"],
        "timelapse": options["timelapse"], "use_ams": options["use_ams"], "layer_inspect": False}}
    if options["use_ams"]:
        # BambuStudio SelectMachine.cpp get_ams_mapping_result: legacy flattened
        # tray mapping plus explicit unit/slot mapping for newer firmware.
        request["print"].update(ams_mapping=[0], ams_mapping2=[{"ams_id": 0, "slot_id": 0}])
    return request


class A1LanTransport:
    """Real, opt-in FTPS/MQTT Developer-LAN prototype, never a cloud fallback.

    Instantiate from trusted deployment configuration and call ONLY through
    submit_print's approval/ledger boundary. ``supported`` means configured
    prototype code, not physical dispatch acceptance or vendor API support.
    """
    name = "a1_developer_lan_prototype"
    simulation = False
    reason = "Explicit pairing, firmware/mode confirmation and reviewed LAN print options are required."

    def __init__(self, profile_path: Path, *, state_dir: Path | None = None,
                 print_options: dict | None = None, timeout: float = 180,
                 bridge: Path | None = None, bridge_sha256: str | None = None,
                 expected_profile_sha256: str | None = None):
        self.profile_path = Path(profile_path)
        self.expected_profile_sha256 = expected_profile_sha256
        self.state_dir = state_dir or Path.home() / ".local/state/justbuild/lan"
        self.timeout = _timeout(timeout)
        self.options = None
        self.supported = False
        # No unverified native bridge can silently bypass this TLS-pinned path.
        if bridge is not None or bridge_sha256 is not None:
            self.reason = "Native SDK bridge is not enabled; this adapter uses pinned FTPS/MQTT directly."
            return
        try:
            profile = load_lan_profile(self.profile_path, expected_sha256=self.expected_profile_sha256)
            self.options = _print_options(print_options)
            self.supported = _public(profile)["status"] == "configured"
        except LanError:
            pass

    def _bound(self, printer_id: str) -> dict:
        if not self.supported:
            raise LanError("LAN_NOT_CONFIGURED", self.reason)
        profile = load_lan_profile(self.profile_path, expected_sha256=self.expected_profile_sha256)
        if not hmac.compare_digest(profile["serial"], printer_id):
            raise LanError("LAN_TARGET_MISMATCH", "Prepared printer identity does not match the paired LAN target.")
        if _public(profile)["status"] != "configured":
            raise LanError("LAN_PAIRING_INCOMPLETE", "The private LAN pairing changed or is incomplete.")
        return profile

    def _state_path(self, remote_job_id: str) -> Path:
        if not isinstance(remote_job_id, str) or not re.fullmatch(r"jb_[0-9a-f]{24}\.gcode\.3mf", remote_job_id):
            raise LanError("LAN_REMOTE_ID", "Unknown LAN file/task identity.")
        return self.state_dir / (remote_job_id + ".json")

    def _read_state(self, remote_job_id: str) -> dict:
        path = self._state_path(remote_job_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise OSError()
                data = handle.read(16385)
            if len(data) > 16384:
                raise ValueError()
            return json.loads(data)
        except Exception:
            raise LanError("LAN_STATE_UNAVAILABLE", "A private persistent LAN submission record is required for reconciliation.") from None

    @staticmethod
    def _ready(printer: dict):
        if (not printer["identity_verified"] or not printer["firmware_matches"]
                or not printer["fresh_status_received"] or printer["state"] not in ("IDLE", "FINISH")):
            raise LanError("LAN_PREFLIGHT_CHANGED", "A fresh matching A1, firmware and idle state are required; no start request was authorized by this check.")

    def _ready_for_options(self, printer: dict, data: bytes):
        self._ready(printer)
        if self.options["use_ams"]:
            if printer.get("ams_slot0") != {"present": True, "material": "PLA"}:
                raise LanError("LAN_AMS_FILAMENT", "A fresh present PLA spool in AMS unit 0 slot 0 is required.")
            _single_pla_3mf(data)

    @staticmethod
    def _observation(state: dict):
        from .transport import Observation
        return Observation(state["state"], state["remote_job_id"],
                           "lan-" + state["last_evidence"], now(), state.get("progress_percent"))

    def submit(self, artifact_path: Path, printer_id: str, idempotency_key: str):
        import fcntl
        import io
        from .slicer import inspect_sliced_3mf
        profile = self._bound(printer_id)
        if not re.fullmatch(r"[0-9a-f]{64}", idempotency_key):
            raise LanError("LAN_IDEMPOTENCY", "A persistent core-generated idempotency key is required.")
        inspection = inspect_sliced_3mf(artifact_path)
        if inspection["gcode_members"] != ["Metadata/plate_1.gcode"]:
            raise LanError("LAN_SINGLE_PLATE", "LAN prototype accepts exactly the reviewed first sliced plate.")
        with artifact_path.open("rb") as source:
            data = source.read(128 * 1024 * 1024 + 1)
        if len(data) > 128 * 1024 * 1024:
            raise LanError("LAN_FILE_LIMIT", "LAN prototype file limit is 128 MiB.")
        if self.options["use_ams"]:
            _single_pla_3mf(data)
        remote = "jb_" + idempotency_key[:24] + ".gcode.3mf"
        state_path = self._state_path(remote)
        state = {"schema_version": "0.1", "remote_job_id": remote, "idempotency_key": idempotency_key,
                 "printer_id_sha256": hashlib.sha256(printer_id.encode()).hexdigest(),
                 "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                 "state": "unknown", "last_evidence": "attempt-" + idempotency_key[:24],
                 "attempted_at": now(), "events": [], "retry_allowed": False,
                 "protocol_compatibility_verified": False, "print_options": self.options}
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        lock_path = self.state_dir / ("printer-" + state["printer_id_sha256"] + ".lock")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LanError("LAN_PRINTER_BUSY", "Another authorized submission is using this paired printer.") from None
            if state_path.exists():
                previous = self._read_state(remote)
                if any(previous.get(k) != state[k] for k in ("idempotency_key", "printer_id_sha256", "sha256", "print_options")):
                    raise LanError("LAN_ALREADY_ATTEMPTED", "A prior attempt exists for different bytes/options; reconcile it without retrying.")
                return self._observation(previous)
            with LanMQTT(profile, min(self.timeout, 15)) as mqtt:
                self._ready_for_options(_read_status(mqtt, profile, min(self.timeout, 15)), data)
                # Durability precedes all file writes and all start requests.
                _private_write(state_path, state)
                try:
                    with PinnedFTP(profile, min(self.timeout, 30)) as ftp:
                        ftp.voidcmd("CWD /")
                        ftp.storbinary("STOR " + remote, io.BytesIO(data))
                        ftp.voidcmd("TYPE I")
                        if ftp.size(remote) != len(data):
                            raise LanError("LAN_UPLOAD_SIZE", "Printer storage did not confirm the uploaded byte count.")
                        readback_hash, count = hashlib.sha256(), 0

                        def readback(chunk):
                            nonlocal count
                            count += len(chunk)
                            if count > len(data):
                                raise LanError("LAN_READBACK_SIZE", "Remote artifact exceeded its reviewed size.")
                            readback_hash.update(chunk)

                        ftp.retrbinary("RETR " + remote, readback)
                        if count != len(data) or readback_hash.hexdigest() != state["sha256"]:
                            raise LanError("LAN_UPLOAD_HASH", "Printer-stored bytes did not match the reviewed SHA-256.")
                    state["events"].append({"stage": "upload", "observed_at": now(), "stored_sha256_verified": True})
                    _private_write(state_path, state)
                    # Recheck after upload, since another client may have started a job.
                    self._ready_for_options(_read_status(mqtt, profile, min(self.timeout, 15)), data)
                    sequence = str(secrets.randbelow(2_000_000_000) + 1)
                    state["sequence_id"] = sequence
                    state["events"].append({"stage": "start_request_intent", "observed_at": now()})
                    _private_write(state_path, state)
                    request = project_file_request(remote_file=remote, sequence=sequence,
                        md5=hashlib.md5(data, usedforsecurity=False).hexdigest(), options=self.options)
                    mqtt.publish(request)
                    state["events"].append({"stage": "mqtt_broker_puback", "observed_at": now(),
                                            "establishes_printer_receipt": False})
                    deadline = time.monotonic() + min(self.timeout, 30)
                    while time.monotonic() < deadline:
                        item = mqtt.read(deadline - time.monotonic())
                        if item is None:
                            break
                        _, retained, message = item
                        reply = message.get("print", {})
                        if (retained or not isinstance(reply, dict) or reply.get("command") != "project_file"
                                or str(reply.get("sequence_id")) != sequence):
                            continue
                        result = str(reply.get("result", "")).lower()
                        if result not in ("success", "ok", "fail"):
                            continue
                        state["state"] = "failed" if result == "fail" else "start_acknowledged"
                        state["last_evidence"] = "device-ack-" + idempotency_key[:24]
                        state["events"].append({"stage": "device_start_rejected" if result == "fail" else "device_start_ack",
                                                "observed_at": now(), "sequence_matches": True})
                        break
                    _private_write(state_path, state)
                except Exception:
                    # An upload or start may have occurred. Never retry here.
                    state["state"] = "unknown"
                    state["events"].append({"stage": "reconciliation_required", "observed_at": now()})
                    _private_write(state_path, state)
                return self._observation(state)
        finally:
            os.close(lock_fd)

    def poll(self, remote_job_id: str, printer_id: str):
        profile = self._bound(printer_id)
        state = self._read_state(remote_job_id)
        if state.get("printer_id_sha256") != hashlib.sha256(printer_id.encode()).hexdigest():
            raise LanError("LAN_TARGET_MISMATCH", "LAN receipt belongs to another printer.")
        with LanMQTT(profile, min(self.timeout, 15)) as mqtt:
            printer = _read_status(mqtt, profile, min(self.timeout, 15))
        if not printer["identity_verified"] or not printer["firmware_matches"]:
            raise LanError("LAN_IDENTITY_CHANGED", "Read-only telemetry no longer matches the paired printer and firmware.")
        markers = {hashlib.sha256(v.encode()).hexdigest() for v in
                   (remote_job_id, remote_job_id.removesuffix(".gcode.3mf"))}
        if not markers.intersection(printer["task_marker_sha256"]):
            # A previous/other print is never evidence of this submission.
            from .transport import Observation
            return Observation("unknown", remote_job_id, "lan-task-not-correlated", now())
        mapped = {"RUNNING": "printing", "PAUSE": "paused", "FINISH": "completed", "FAILED": "failed"}
        observed = mapped.get(printer["state"], "unknown")
        from .transport import Observation
        return Observation(observed, remote_job_id, "lan-correlated-status-" + state["idempotency_key"][:24],
                           printer["observed_at"], printer["progress_percent"])
