"""LAN wire-path harness only: no real device, access code or print is used."""
from collections import deque
import hashlib
import inspect
import io
import json
from pathlib import Path
import stat
import time
import zipfile

import pytest

from justbuild.printing import lan


OPTIONS = {"bed_leveling": True, "flow_calibration": False, "vibration_calibration": False,
           "timelapse": False, "use_ams": False, "bed_type": "textured_plate"}


@pytest.fixture
def profile(tmp_path):
    # Deliberately fake fixture identities; never credentials from a real printer.
    value = {"host": "127.0.0.1", "serial": "FAKE-A1-0001", "access_code": "FAKECODE",
             "firmware": "fixture-01", "mqtt_certificate_sha256": "a" * 64,
             "ftp_certificate_sha256": "b" * 64, "authorization_mode": "developer_lan",
             "developer_mode_confirmed": True}
    path = tmp_path / "private/a1.json"
    lan.save_lan_profile(value, path=path)
    return value, path


def test_private_atomic_profile_and_public_redaction(profile):
    value, path = profile
    public = lan.save_lan_profile(value, path=path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert lan.load_lan_profile(path)["access_code"] == "FAKECODE"
    assert "FAKECODE" not in json.dumps(public) and "FAKE-A1-0001" not in json.dumps(public)
    assert public["status"] == "configured" and public["dispatch_verified"] is False
    assert not list(path.parent.glob(".lan-*"))


@pytest.mark.parametrize("field,value", [("host", "example.com"), ("serial", "bad/path"),
                                        ("access_code", "secret\n"), ("mqtt_port", True),
                                        ("ftp_certificate_sha256", "not-a-hash"),
                                        ("authorization_mode", "bypass"), ("firmware", "x\n")])
def test_invalid_pairing_values(profile, field, value):
    values, path = profile
    values[field] = value
    with pytest.raises(lan.LanError): lan.save_lan_profile(values, path=path)


def test_world_readable_profile_and_symlinks_are_rejected(profile, tmp_path):
    _, path = profile
    path.chmod(0o644)
    with pytest.raises(lan.LanError): lan.load_lan_profile(path)
    path.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(lan.LanError): lan.load_lan_profile(link)


def test_reviewed_pairing_hash_blocks_same_serial_connection_change(wire):
    state, _, artifact, values, path = wire
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    adapter = lan.A1LanTransport(path, state_dir=path.parent / "state", print_options=OPTIONS,
                                 expected_profile_sha256=digest)
    assert adapter.supported is True
    lan.save_lan_profile({**values, "host": "127.0.0.2"}, path=path)
    with pytest.raises(lan.LanError, match="changed after review"):
        adapter.submit(artifact, values["serial"], "a" * 64)
    assert state["messages"] == [] and state["ftp"] == []


def test_pairing_mutation_during_parse_fails_before_return(profile, monkeypatch):
    values, path = profile
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original = lan._profile

    def change_after_parse(value):
        parsed = original(value)
        path.write_text(json.dumps({**values, "firmware": "changed-fixture"}))
        return parsed

    monkeypatch.setattr(lan, "_profile", change_after_parse)
    with pytest.raises(lan.LanError, match="during review validation"):
        lan.load_lan_profile(path, expected_sha256=digest)


def test_pairing_snapshot_is_frozen_after_final_hash(profile, monkeypatch):
    values, path = profile
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original = lan._profile_bytes
    reads = 0

    def change_after_read(chosen):
        nonlocal reads
        data = original(chosen)
        reads += 1
        if reads == 2:
            path.write_text(json.dumps({**values, "host": "127.0.0.2"}))
        return data

    monkeypatch.setattr(lan, "_profile_bytes", change_after_read)
    frozen = lan.load_lan_profile(path, expected_sha256=digest)
    assert frozen["host"] == "127.0.0.1"
    assert json.loads(path.read_text())["host"] == "127.0.0.2"


def test_reviewed_pairing_hash_validation_fails_closed(profile):
    _, path = profile
    for digest in ("not-a-hash", "f" * 64):
        assert lan.A1LanTransport(path, print_options=OPTIONS,
                                  expected_profile_sha256=digest).supported is False


def test_public_profile_status_is_local_redacted_and_does_not_connect(profile, monkeypatch, tmp_path):
    _, path = profile
    monkeypatch.setattr(lan, "LanMQTT", lambda *_: pytest.fail("Status must never connect"))
    result = lan.lan_profile_status(path=path)
    assert result["configured"] is True
    assert result["ready_prerequisites"]["developer_lan_user_confirmed"] is True
    assert result["network_check_performed"] is False
    assert "FAKECODE" not in json.dumps(result) and "FAKE-A1-0001" not in json.dumps(result)
    absent = lan.lan_profile_status(path=tmp_path / "missing.json")
    assert absent["configured"] is False and absent["status"] == "needs_input"


@pytest.mark.parametrize("field,value", [("firmware", ""), ("authorization_mode", "unknown"),
                                        ("developer_mode_confirmed", False), ("mqtt_certificate_sha256", "")])
def test_missing_firmware_mode_or_pin_never_connects(profile, monkeypatch, field, value):
    values, path = profile
    values[field] = value
    lan.save_lan_profile(values, path=path)
    monkeypatch.setattr(lan, "LanMQTT", lambda *_: pytest.fail("Incomplete pairing must not connect"))
    result = lan.check_lan_connection(profile_path=path)
    assert result["status"] == "needs_input"
    assert result["print_command_sent"] is False
    assert result["connection_verified"] is False and result["ready_for_new_print"] is False


@pytest.fixture
def wire(profile, tmp_path, monkeypatch):
    values, path = profile
    state = {"messages": [], "ftp": [], "files": {}, "state": "IDLE", "firmware": "fixture-01",
             "serial": values["serial"], "model": "N2S", "ack": "success", "wrong_sequence": False,
             "bad_readback": False, "task_name": "", "retained": False}

    class MQTT:
        def __init__(self, *_args): self.pending = deque()
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def publish(self, request, *, qos=1):
            state["messages"].append(request)
            if "info" in request:
                self.pending.append({"info": {"command": "get_version", "sequence_id": request["info"]["sequence_id"],
                    "module": [{"name": "ota", "sw_ver": state["firmware"], "hw_ver": state["model"], "sn": state["serial"]}]}})
            elif "pushing" in request:
                self.pending.append({"print": {"command": "push_status", "gcode_state": state["state"],
                    "subtask_name": state["task_name"], "mc_percent": 37, "ams": state.get("ams", {})}})
            elif state["ack"] is not None:
                self.pending.append({"print": {"command": "project_file", "result": state["ack"],
                    "sequence_id": "wrong" if state["wrong_sequence"] else request["print"]["sequence_id"]}})
            return {"qos": qos, "stage": "local_send_completed" if qos == 0 else "mqtt_broker_puback",
                    "broker_publication_acknowledged": qos == 1, "establishes_printer_receipt": False}
        def read(self, _timeout):
            return (time.monotonic(), state["retained"], self.pending.popleft()) if self.pending else None

    class FTP:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def voidcmd(self, command): state["ftp"].append(command)
        def storbinary(self, command, source):
            state["ftp"].append(command)
            state["files"][command[5:]] = source.read()
            # A durable attempt exists before the first byte is uploaded.
            assert list((tmp_path / "state").glob("*.json"))
        def size(self, name): return len(state["files"][name])
        def retrbinary(self, command, callback):
            state["ftp"].append(command)
            callback(b"wrong" if state["bad_readback"] else state["files"][command[5:]])

    monkeypatch.setattr(lan, "LanMQTT", MQTT)
    monkeypatch.setattr(lan, "PinnedFTP", FTP)
    artifact = tmp_path / "reviewed.gcode.3mf"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; harmless transport fixture, never executed\n")
    adapter = lan.A1LanTransport(path, state_dir=tmp_path / "state", print_options=OPTIONS, timeout=1)
    return state, adapter, artifact, values, path


def test_connection_check_only_requests_read_only_info_and_noop(wire):
    state, _, _, _, path = wire
    result = lan.check_lan_connection(profile_path=path, timeout=1)
    assert result["status"] == "ok"
    assert result["connection_verified"] is True and result["ready_for_new_print"] is True
    assert result["printer"]["identity_verified"] is True
    assert result["certificate_pins_verified"] is True
    assert state["ftp"] == ["NOOP"]
    assert all(set(message) in ({"info"}, {"pushing"}) for message in state["messages"])
    assert result["dispatch_verified"] is False and result["print_command_sent"] is False
    assert "FAKECODE" not in json.dumps(result) and "FAKE-A1-0001" not in json.dumps(result)


def test_busy_printer_is_connected_but_not_ready_for_a_new_print(wire):
    state, _, _, _, path = wire
    state["state"] = "RUNNING"
    result = lan.check_lan_connection(profile_path=path, timeout=1)
    assert result["status"] == "needs_input"
    assert result["connection_verified"] is True and result["ready_for_new_print"] is False
    assert result["mqtt_authenticated"] is True and result["ftps_authenticated"] is True
    assert result["issues"][0]["code"] == "LAN_NOT_IDLE"
    assert result["printer"]["state"] == "RUNNING"
    assert result["dispatch_verified"] is False and result["print_command_sent"] is False
    assert state["ftp"] == ["NOOP"]
    assert all(set(message) in ({"info"}, {"pushing"}) for message in state["messages"])


@pytest.mark.parametrize("field,value", [("firmware", "other"), ("serial", "OTHER-SERIAL"),
                                        ("model", "A1-MINI"), ("state", "RUNNING"), ("retained", True)])
def test_preflight_identity_firmware_busy_and_retained_status_block_print(wire, field, value):
    state, adapter, artifact, values, path = wire
    state[field] = value
    assert lan.check_lan_connection(profile_path=path, timeout=1)["status"] == "needs_input"
    with pytest.raises(lan.LanError): adapter.submit(artifact, values["serial"], "d" * 64)
    assert not any(command.startswith("STOR ") for command in state["ftp"])
    assert not any("print" in message for message in state["messages"])


def test_real_transport_code_uploads_exact_bytes_readbacks_hash_and_requires_device_ack(wire):
    state, adapter, artifact, values, _ = wire
    result = adapter.submit(artifact, values["serial"], "d" * 64)
    assert adapter.simulation is False and result.state == "start_acknowledged"
    assert state["files"][result.remote_job_id] == artifact.read_bytes()
    assert any(command.startswith("RETR ") for command in state["ftp"])
    commands = [message["print"] for message in state["messages"] if "print" in message]
    assert len(commands) == 1 and commands[0]["command"] == "project_file"
    assert commands[0]["url"] == "ftp:///" + result.remote_job_id
    assert commands[0]["param"] == "Metadata/plate_1.gcode"
    assert commands[0]["md5"] == hashlib.md5(artifact.read_bytes(), usedforsecurity=False).hexdigest()
    assert "FAKECODE" not in json.dumps(commands)
    receipt = adapter._read_state(result.remote_job_id)
    assert receipt["events"][0]["stage"] == "upload"
    assert receipt["events"][0]["stored_sha256_verified"] is True
    assert receipt["events"][2]["stage"] == "mqtt_broker_puback"
    assert receipt["events"][2]["establishes_printer_receipt"] is False
    assert "FAKECODE" not in json.dumps(receipt) and values["serial"] not in json.dumps(receipt)
    assert adapter.submit(artifact, values["serial"], "d" * 64).state == "start_acknowledged"
    assert len([m for m in state["messages"] if "print" in m]) == 1


@pytest.mark.parametrize("change", ["missing_ack", "wrong_sequence", "failed_ack", "bad_readback"])
def test_missing_ack_or_mismatching_bytes_never_claim_started_and_never_retry(wire, change):
    state, adapter, artifact, values, _ = wire
    if change == "missing_ack": state["ack"] = None
    elif change == "wrong_sequence": state["wrong_sequence"] = True
    elif change == "failed_ack": state["ack"] = "fail"
    else: state["bad_readback"] = True
    result = adapter.submit(artifact, values["serial"], "e" * 64)
    assert result.state == ("failed" if change == "failed_ack" else "unknown")
    before = len([m for m in state["messages"] if "print" in m])
    adapter.submit(artifact, values["serial"], "e" * 64)
    assert len([m for m in state["messages"] if "print" in m]) == before
    if change == "bad_readback": assert before == 0


def test_poll_requires_matching_task_and_does_not_send_control(wire):
    state, adapter, artifact, values, _ = wire
    result = adapter.submit(artifact, values["serial"], "f" * 64)
    state["state"] = "RUNNING"
    state["task_name"] = "somebody-elses-task"
    assert adapter.poll(result.remote_job_id, values["serial"]).state == "unknown"
    state["task_name"] = result.remote_job_id.removesuffix(".gcode.3mf")
    observed = adapter.poll(result.remote_job_id, values["serial"])
    assert observed.state == "printing" and observed.progress_percent == 37
    state["state"] = "FINISH"
    assert adapter.poll(result.remote_job_id, values["serial"]).state == "completed"
    assert len([message for message in state["messages"] if "print" in message]) == 1


def test_raw_control_or_ams_options_are_not_accepted(profile):
    _, path = profile
    assert lan.A1LanTransport(path).supported is False
    assert lan.A1LanTransport(path, print_options={**OPTIONS, "gcode": "G28"}).supported is False
    assert lan.A1LanTransport(path, print_options={**OPTIONS, "use_ams": True}).supported is False
    with pytest.raises(lan.LanError):
        lan.project_file_request(remote_file="../../evil", sequence="1", md5="a" * 32, options=OPTIONS)


def test_tls_fingerprint_mismatch_precedes_ftp_authentication(profile, monkeypatch):
    _, path = profile
    values = lan.load_lan_profile(path)
    attempts = []

    class Socket:
        family = 2
        def getpeercert(self, binary_form=False): return b"different certificate"
        def close(self): pass

    class Context:
        def wrap_socket(self, *_args, **_kwargs): return Socket()

    monkeypatch.setattr(lan, "_context", Context)
    monkeypatch.setattr(lan.socket, "create_connection", lambda *_args, **_kwargs: Socket())
    monkeypatch.setattr(lan.PinnedFTP, "login", lambda *_args: attempts.append("credentials"))
    with pytest.raises(lan.LanError, match="certificate"):
        with lan.PinnedFTP(values, 1): pass
    assert attempts == []


def test_paho_pin_hook_is_before_connect_credentials_on_the_supported_dependency():
    mqtt = pytest.importorskip("paho.mqtt.client")
    source = inspect.getsource(mqtt.Client.reconnect)
    assert source.index("self._call_socket_open(self._sock)") < source.index("self._send_connect(")


def _paho_fixture_connection(profile, *, response="valid", ack_qos1=False):
    """Exercise LanMQTT.publish with actual Paho message-info state, no sockets."""
    paho = pytest.importorskip("paho.mqtt.client")
    values, path = profile
    connection = lan.LanMQTT(lan.load_lan_profile(path), .02)

    class FakeClient:
        def __init__(self): self.sent = []

        def publish(self, topic, payload, *, qos, retain):
            self.sent.append((topic, json.loads(payload), qos, retain))
            info = paho.MQTTMessageInfo(len(self.sent))
            info.rc = paho.MQTT_ERR_SUCCESS
            # Paho marks QoS 0 after all socket bytes are written. QoS 1 is
            # marked only on PUBACK; exercise the distinct evidence meanings.
            if qos == 0 or ack_qos1:
                info._set_as_published()
            value = json.loads(payload)
            if response != "absent":
                if "info" in value:
                    seq = "wrong" if response == "wrong_sequence" else value["info"]["sequence_id"]
                    report = {"info": {"command": "get_version", "sequence_id": seq,
                        "module": [{"name": "ota", "sn": values["serial"], "hw_ver": "N2S",
                                    "sw_ver": values["firmware"]}]}}
                else:
                    report = {"print": {"gcode_state": "IDLE"}}
                timestamp = time.monotonic() - (100 if response == "stale" else 0)
                connection.messages.put((timestamp, response == "retained", report))
            return info

    connection.client = FakeClient()
    return connection


def test_actual_paho_message_info_qos1_without_puback_never_downgrades_or_retries(profile):
    mqtt = _paho_fixture_connection(profile, response="absent")
    with pytest.raises(lan.LanError) as error:
        mqtt.publish({"info": {"command": "get_version", "sequence_id": "1"}})
    assert error.value.code == "LAN_MQTT_PUBACK"
    assert [message[2] for message in mqtt.client.sent] == [1]


def test_actual_paho_message_info_qos0_queries_require_fresh_device_responses(profile):
    mqtt = _paho_fixture_connection(profile)
    result = lan._read_status(mqtt, lan.load_lan_profile(profile[1]), .05)
    assert result["identity_verified"] is True and result["version_reply_received"] is True
    assert result["fresh_status_received"] is True and result["state"] == "IDLE"
    assert [message[2] for message in mqtt.client.sent] == [0, 0]
    assert all(not message[3] for message in mqtt.client.sent)
    assert all(item == {"qos": 0, "stage": "local_send_completed",
                       "broker_publication_acknowledged": False, "establishes_printer_receipt": False}
               for item in result["query_publications"])


@pytest.mark.parametrize("response", ["absent", "wrong_sequence", "retained", "stale"])
def test_qos0_local_send_alone_cannot_establish_identity_or_readiness(profile, response):
    mqtt = _paho_fixture_connection(profile, response=response)
    result = lan._read_status(mqtt, lan.load_lan_profile(profile[1]), .02)
    assert result["version_reply_received"] is False and result["identity_verified"] is False
    assert all(item["stage"] == "local_send_completed" for item in result["query_publications"])
    with pytest.raises(lan.LanError): lan.A1LanTransport._ready(result)


def test_qos1_fast_puback_is_retained_and_evidence_does_not_claim_device_receipt(profile):
    mqtt = _paho_fixture_connection(profile, response="absent", ack_qos1=True)
    result = mqtt.publish({"info": {"command": "get_version", "sequence_id": "1"}})
    assert result["stage"] == "mqtt_broker_puback" and result["qos"] == 1
    assert result["broker_publication_acknowledged"] is True
    assert result["establishes_printer_receipt"] is False


@pytest.mark.parametrize("qos", [True, -1, 2, "0"])
def test_unsupported_or_ambiguous_qos_is_rejected_before_publish(profile, qos):
    mqtt = _paho_fixture_connection(profile, response="absent")
    with pytest.raises(lan.LanError): mqtt.publish({}, qos=qos)
    assert mqtt.client.sent == []


AMS_OPTIONS = {**OPTIONS, "use_ams": True, "ams_mapping": [0]}


def _ams_status():
    return {"ams_exist_bits": "1", "tray_exist_bits": "1", "ams": [
        {"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tag_uid": "private-fixture-rfid"}]}]}


def _pla_artifact(path, *, material="PLA", count=1):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; offline harmless fixture\n")
        archive.writestr("Metadata/project_settings.config", json.dumps({"filament_type": [material] * count}))
        archive.writestr("Metadata/slice_info.config", '<config><plate><metadata key="filament_maps" value="1"/>' +
            ''.join('<filament id="%s" type="%s"/>' % (i + 1, material) for i in range(count)) +
            '<layer_filament_lists><layer_filament_list filament_list="0"/></layer_filament_lists></plate></config>')


def test_single_ams_options_are_explicit_and_copied():
    validated = lan.validate_lan_print_options(AMS_OPTIONS)
    validated["ams_mapping"].append(1)
    assert AMS_OPTIONS["ams_mapping"] == [0]
    request = lan.project_file_request(remote_file="jb_" + "a" * 24 + ".gcode.3mf",
        sequence="1", md5="b" * 32, options=AMS_OPTIONS)["print"]
    assert request["use_ams"] is True and request["ams_mapping"] == [0]
    assert request["ams_mapping2"] == [{"ams_id": 0, "slot_id": 0}]
    external = lan.project_file_request(remote_file="jb_" + "a" * 24 + ".gcode.3mf",
        sequence="1", md5="b" * 32, options=OPTIONS)["print"]
    assert not external["use_ams"] and "ams_mapping" not in external and "ams_mapping2" not in external


@pytest.mark.parametrize("mapping", [[True], [False], ["0"], [1], [], [0, 1], (0,), None])
def test_ams_mapping_rejects_ambiguous_or_other_slots(mapping):
    with pytest.raises(lan.LanError): lan.validate_lan_print_options({**AMS_OPTIONS, "ams_mapping": mapping})


def test_ams_status_is_fixed_slot_and_redacted(wire):
    state, _, _, _, path = wire
    state["ams"] = _ams_status()
    report = lan.check_lan_connection(profile_path=path, timeout=1)
    assert report["printer"]["ams_slot0"] == {"present": True, "material": "PLA"}
    assert "private-fixture-rfid" not in json.dumps(report)
    state["ams"]["tray_exist_bits"] = "0"
    assert lan.check_lan_connection(profile_path=path, timeout=1)["printer"]["ams_slot0"]["present"] is False


def test_ams_submit_requires_single_pla_before_network(wire):
    state, _, artifact, values, path = wire
    adapter = lan.A1LanTransport(path, state_dir=artifact.parent / "state", print_options=AMS_OPTIONS, timeout=1)
    for material, count in [("PETG", 1), ("PLA", 2)]:
        _pla_artifact(artifact, material=material, count=count)
        with pytest.raises(lan.LanError) as error: adapter.submit(artifact, values["serial"], "a" * 64)
        assert error.value.code == "LAN_AMS_SINGLE_PLA"
    assert state["messages"] == [] and state["ftp"] == []


@pytest.mark.parametrize("change", ["missing", "petg", "wrong_unit", "wrong_slot", "bool_bits"])
def test_ams_slot_must_be_present_and_pla_before_upload(wire, change):
    state, _, artifact, values, path = wire
    state["ams"] = _ams_status()
    if change == "missing": state["ams"]["tray_exist_bits"] = "0"
    if change == "petg": state["ams"]["ams"][0]["tray"][0]["tray_type"] = "PETG"
    if change == "wrong_unit": state["ams"]["ams"][0]["id"] = "1"
    if change == "wrong_slot": state["ams"]["ams"][0]["tray"][0]["id"] = "1"
    if change == "bool_bits": state["ams"]["ams_exist_bits"] = True
    _pla_artifact(artifact)
    adapter = lan.A1LanTransport(path, state_dir=artifact.parent / "state", print_options=AMS_OPTIONS, timeout=1)
    with pytest.raises(lan.LanError) as error: adapter.submit(artifact, values["serial"], "a" * 64)
    assert error.value.code == "LAN_AMS_FILAMENT"
    assert not state["ftp"] and not any("print" in m for m in state["messages"])


def test_ams_submit_sends_mapping_once_and_never_retries(wire):
    state, _, artifact, values, path = wire
    state["ams"] = _ams_status()
    _pla_artifact(artifact)
    adapter = lan.A1LanTransport(path, state_dir=artifact.parent / "state", print_options=AMS_OPTIONS, timeout=1)
    assert adapter.supported
    result = adapter.submit(artifact, values["serial"], "a" * 64)
    assert result.state == "start_acknowledged"
    assert adapter.submit(artifact, values["serial"], "a" * 64).state == "start_acknowledged"
    commands = [m["print"] for m in state["messages"] if "print" in m]
    assert len(commands) == 1 and commands[0]["ams_mapping"] == [0]
    assert sum("pushing" in m for m in state["messages"]) == 2


def test_ams_slot_rechecked_after_upload_before_start(wire, monkeypatch):
    state, _, artifact, values, path = wire
    state["ams"] = _ams_status()
    _pla_artifact(artifact)
    original = lan._read_status
    reads = 0
    def read_status(*args):
        nonlocal reads
        reads += 1
        if reads == 2: state["ams"]["tray_exist_bits"] = "0"
        return original(*args)
    monkeypatch.setattr(lan, "_read_status", read_status)
    adapter = lan.A1LanTransport(path, state_dir=artifact.parent / "state", print_options=AMS_OPTIONS, timeout=1)
    assert adapter.submit(artifact, values["serial"], "a" * 64).state == "unknown"
    assert any(command.startswith("STOR ") for command in state["ftp"])
    assert not any("print" in m for m in state["messages"])
    assert adapter.submit(artifact, values["serial"], "a" * 64).state == "unknown"
    assert reads == 2
