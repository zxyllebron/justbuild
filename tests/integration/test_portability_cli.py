"""Public CLI outcomes distinguish optional inventory, CAD failure and incomplete work."""
import json
from pathlib import Path
import sys

import pytest

from justbuild import cli, diagnostics, selfcheck


@pytest.mark.parametrize('core,expected', [('ok', 0), ('needs_input', 2)])
def test_doctor_exit_only_tracks_core_inventory(monkeypatch, capsys, core, expected):
    report = {'status': core, 'core': {'status': core}, 'optional': {'slicer': {'status': 'needs_input'}}}
    monkeypatch.setattr(diagnostics, 'doctor_report', lambda: report)
    monkeypatch.setattr(sys, 'argv', ['justbuild', 'doctor'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == expected
    assert json.loads(capsys.readouterr().out) == report


@pytest.mark.parametrize('verdict,expected', [('pass', 0), ('fail', 1), ('incomplete', 2)])
def test_self_test_cli_propagates_real_verdict(monkeypatch, capsys, tmp_path, verdict, expected):
    seen = []
    report = {'status': {'pass': 'ok', 'fail': 'failed', 'incomplete': 'needs_input'}[verdict], 'verdict': verdict}
    def run(destination):
        seen.append(destination)
        return report
    monkeypatch.setattr(selfcheck, 'run_self_check', run)
    target = tmp_path / 'selftest-001'
    monkeypatch.setattr(sys, 'argv', ['justbuild', 'self-test', '--out', str(target)])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == expected
    assert seen == [target]
    assert json.loads(capsys.readouterr().out) == report


def test_self_test_requires_explicit_output_before_execution(monkeypatch):
    def forbidden(_destination):
        pytest.fail('Must not start self-test without an explicit destination')
    monkeypatch.setattr(selfcheck, 'run_self_check', forbidden)
    monkeypatch.setattr(sys, 'argv', ['justbuild', 'self-test'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2


def test_self_test_reports_refused_output_without_traceback(monkeypatch, capsys):
    def refused(_destination):
        raise ValueError('Self-test output must be an empty or new directory')
    monkeypatch.setattr(selfcheck, 'run_self_check', refused)
    monkeypatch.setattr(sys, 'argv', ['justbuild', 'self-test', '--out', 'existing'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    streams = capsys.readouterr()
    assert not streams.out
    assert 'empty or new directory' in streams.err
    assert 'Traceback' not in streams.err
