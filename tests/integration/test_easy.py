"""Easy-entry contracts: one tiny real STEP intake, no service/GUI/hardware start."""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import runpy
import shlex
import socket
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest

from justbuild import cli, easy
from justbuild.orchestrator import JobManager

BASE = "http://127.0.0.1:18765"


def forbidden(*_args, **_kwargs):
    pytest.fail("Easy tests must not execute shell, provider, print, service or GUI actions")


@pytest.fixture(autouse=True)
def action_boundaries(monkeypatch):
    from justbuild import providers, slicing_config
    from justbuild.printing import jobs, lan
    from justbuild.printing.slicer import BambuCliSlicer
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(easy.webbrowser, "open", forbidden)
    monkeypatch.setattr(providers, "propose", forbidden)
    monkeypatch.setattr(jobs, "submit_print", forbidden)
    monkeypatch.setattr(lan.A1LanTransport, "submit", forbidden)
    monkeypatch.setattr(lan, "check_lan_connection", forbidden)
    monkeypatch.setattr(BambuCliSlicer, "slice", forbidden)
    # Per-test replacement avoids reading any operator's optional slicer setup.
    monkeypatch.setattr(slicing_config, "SlicingConfig", lambda: SimpleNamespace(slicer=None, resources=None))
    for name in ("confirm", "run", "_stage"):
        monkeypatch.setattr(JobManager, name, forbidden)


@pytest.fixture(scope="module")
def small_step(tmp_path_factory):
    pytest.importorskip("cadquery")
    root = tmp_path_factory.mktemp("easy-real-step")
    make_fixture = runpy.run_path(str(Path(__file__).parents[1] / "cad/synthetic_fixture.py"))["make_fixture"]
    request = make_fixture(root)
    return request.parent / "synthetic_board.step"


def answers(monkeypatch, sequence):
    iterator = iter(sequence)
    def read(_prompt=""):
        value = next(iterator)
        if isinstance(value, BaseException): raise value
        return value
    monkeypatch.setattr(builtins, "input", read)


@pytest.mark.parametrize("ending", ["0", EOFError(), KeyboardInterrupt()])
def test_chinese_menu_exit_and_eof_never_start_service(monkeypatch, capsys, ending):
    monkeypatch.setattr(easy, "_service", forbidden)
    answers(monkeypatch, [ending])
    assert easy.run_easy(open_browser=False) == 0
    text = capsys.readouterr().out
    assert "板卡外壳助手" in text and "新建外壳" in text and "审阅后打印" in text
    if isinstance(ending, BaseException): assert "已退出；未启动打印" in text


def test_invalid_choice_recovers_and_invalid_path_returns_to_menu(monkeypatch, capsys):
    monkeypatch.setattr(easy, "_service", forbidden)
    answers(monkeypatch, ["9", "1", "/definitely-missing/board.step", "0"])
    assert easy.run_easy(open_browser=False) == 0
    text = capsys.readouterr().out
    assert "请输入 0、1、2、3 或 4" in text and "未完成" in text
    assert text.count("新建外壳") >= 3


@pytest.mark.parametrize("form", ["plain", "single_quotes", "double_quotes", "escaped_spaces", "file_uri", "localhost_file_uri"])
def test_drag_drop_and_local_file_path_forms(small_step, tmp_path, form):
    source = tmp_path / "板 卡 V1.STP"; source.write_bytes(small_step.read_bytes())
    raw = str(source)
    if form == "single_quotes": raw = shlex.quote(raw)
    elif form == "double_quotes": raw = '"' + raw + '"'
    elif form == "escaped_spaces": raw = raw.replace(" ", "\\ ")
    elif form == "file_uri": raw = source.as_uri()
    elif form == "localhost_file_uri": raw = source.as_uri().replace("file:///", "file://localhost/", 1)
    assert easy.step_path(raw) == source.resolve()


@pytest.mark.parametrize("raw", ["file://remote.example/tmp/board.step", "file:///tmp/board.step?x=1", "file:///tmp/board.step#claim=bad", "'unterminated.step"])
def test_remote_or_ambiguous_file_input_is_rejected(raw):
    with pytest.raises(ValueError): easy.step_path(raw)


def test_shell_syntax_is_literal_and_never_executed(small_step, tmp_path):
    marker = tmp_path / "PWNED"
    # A valid existing filename can literally contain shell metacharacters.
    source = tmp_path / "$(touch PWNED);`whoami`.step"
    source.write_bytes(small_step.read_bytes())
    assert easy.step_path(str(source)) == source.resolve()
    assert easy.step_path(shlex.quote(str(source))) == source.resolve()
    with pytest.raises(ValueError): easy.step_path(str(small_step) + "; touch " + str(marker))
    assert not marker.exists()


def test_real_step_submission_keeps_proposal_unconfirmed_and_returns_local_owner_link(small_step, tmp_path, monkeypatch, capsys):
    jobs = tmp_path / "data/jobs"
    service_calls = []
    def service(port, data_dir):
        service_calls.append((port, data_dir))
        return {"data_dir": str(jobs), "base_url": BASE, "started": False}
    monkeypatch.setattr(easy, "_service", service)
    # An existing held result must not be modified when adding a different board.
    old = jobs / ("a" * 32); old.mkdir(parents=True)
    old_job = old / "job.json"; old_job.write_text(json.dumps({"job_id": old.name, "print_hold": {"reason": "review_required"}}))
    old_bytes = old_job.read_bytes()
    requirements = "上下盖螺丝固定，USB外露；先让我确认所有孔和接口。"
    result = easy.submit_step(small_step, requirements, port=18765, data_dir=tmp_path / "requested-root", open_browser=False)
    assert service_calls == [(18765, tmp_path / "requested-root")]
    assert result["state"] == "awaiting_confirmation" and result["print_started"] is False
    parsed = urlparse(result["local_owner_url"])
    assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:18765"
    assert parse_qs(parsed.query) == {"job": [result["job_id"]]}
    assert len(parse_qs(parsed.fragment)["claim"][0]) >= 32
    folder = jobs / result["job_id"]
    job = json.loads((folder / "job.json").read_text())
    proposal = json.loads((folder / "proposal.json").read_text())
    assert job["user_requirements"] == requirements
    assert proposal["request"]["constraints"]["confirmed"] is False
    assert len(proposal["inventory"]) == 2 and all(item["valid"] for item in proposal["inventory"])
    assert job["stage_results"] == {} and job["artifacts"] == []
    assert not (folder / "request.json").exists()
    assert not any((folder / name).exists() for name in ("generating", "verifying", "slicing", "submitting"))
    assert old_job.read_bytes() == old_bytes
    text = capsys.readouterr().out
    assert result["local_owner_url"] in text and "一次性本机管理链接" in text
    assert "没有启动打印" in text


def test_no_open_does_not_call_browser(monkeypatch):
    monkeypatch.setattr(easy.webbrowser, "open", forbidden)
    assert easy._open(BASE, False) is False


def test_existing_held_design_is_read_only(tmp_path, monkeypatch, capsys):
    jobs = tmp_path / "jobs"; folder = jobs / ("b" * 32); folder.mkdir(parents=True)
    job = {"job_id": folder.name, "label": "已有板卡", "updated_at": "2026-10-26", "owner_sha256": "1" * 64,
           "print_hold": {"reason": "new_revision_review"}, "artifacts": [{"kind": "review_glb_light"}],
           "share_url": "/?job=" + folder.name + "&view=readonly"}
    path = folder / "job.json"; path.write_text(json.dumps(job)); original = path.read_bytes()
    monkeypatch.setattr(easy, "_service", lambda *_: {"data_dir": str(jobs), "base_url": BASE, "started": False})
    answers(monkeypatch, ["1"])
    easy._existing(18765, jobs, False)
    assert path.read_bytes() == original
    text = capsys.readouterr().out
    assert "等待新版审阅" in text and "不会重新生成、解除审阅限制或启动打印" in text
    assert BASE + job["share_url"] in text


@pytest.mark.parametrize("argv", [[], ["easy"], ["board.STEP"], ["easy", "board.step", "--requirements", "USB外露", "--no-open"]])
def test_cli_default_easy_direct_step_and_no_open_routing(monkeypatch, argv):
    calls = []
    monkeypatch.setattr(easy, "run_easy", lambda **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr("sys.argv", ["justbuild", *argv])
    with pytest.raises(SystemExit) as ended: cli.main()
    assert ended.value.code == 0 and len(calls) == 1
    expected_step = "board.STEP" if argv == ["board.STEP"] else "board.step" if "board.step" in argv else None
    assert calls[0]["step"] == expected_step
    assert calls[0]["open_browser"] is ("--no-open" not in argv)
    assert calls[0]["port"] == 18765
    if "--requirements" in argv: assert calls[0]["requirements"] == "USB外露"


def test_legacy_doctor_remains_json_and_does_not_enter_easy(monkeypatch, capsys):
    monkeypatch.setattr(easy, "run_easy", forbidden)
    monkeypatch.setattr("sys.argv", ["justbuild", "doctor"])
    with pytest.raises(SystemExit) as ended: cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "doctor"
    assert report["check_scope"]["programs_executed"] is False
    assert report["check_scope"]["network_access_performed"] is False
    assert ended.value.code == (0 if report["core"]["status"] == "ok" else 2)


@pytest.fixture
def workspace(tmp_path):
    from justbuild.server import create_app
    root = tmp_path / "selected-workspace/jobs"
    app = create_app(root, local_pairing_enabled=False, lan_printing_enabled=False)
    try: yield app, root.resolve()
    finally: app.state.manager.pool.shutdown(wait=True)


@pytest.mark.parametrize("peer,host", [("127.0.0.1", "127.0.0.1:18765"), ("::1", "[::1]:18765"), ("127.0.0.1", "localhost:18765")])
def test_workspace_loopback_read_returns_actual_root(workspace, peer, host):
    app, root = workspace
    with TestClient(app, base_url=BASE, client=(peer, 43210)) as client:
        response = client.get("/api/local/workspace", headers={"Host": host})
    assert response.status_code == 200
    assert response.json() == {"product": "justbuild", "interface": "easy-v1", "data_dir": str(root)}
    assert not list(root.glob("*/job.json"))


@pytest.mark.parametrize("peer,headers", [
    ("10.17.36.20", {}),
    ("127.0.0.1", {"Host": "evil.example"}),
    ("127.0.0.1", {"Host": "localhost.evil:18765"}),
    ("127.0.0.1", {"Forwarded": "for=127.0.0.1"}),
    ("127.0.0.1", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", {"X-Real-IP": "127.0.0.1"}),
    ("127.0.0.1", {"Origin": "https://evil.example"}),
    ("127.0.0.1", {"Sec-Fetch-Site": "cross-site"}),
])
def test_workspace_rejects_remote_proxy_host_and_cross_origin(workspace, peer, headers):
    app, root = workspace
    with TestClient(app, base_url=BASE, client=(peer, 43210)) as client:
        response = client.get("/api/local/workspace", headers=headers)
    assert response.status_code == 403
    assert str(root) not in response.text
