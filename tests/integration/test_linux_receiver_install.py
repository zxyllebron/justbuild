"""Private temp-home installation tests; no real MIME registration or GUI launch."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import quote

import pytest

import justbuild.receiver_install as module


class Mime:
    def __init__(self, current=''):
        self.current, self.calls = current, []

    def __call__(self, args, **kwargs):
        assert kwargs['shell'] is False
        self.calls.append(list(args))
        if args[:2] == ['query', 'default']:
            return SimpleNamespace(returncode=0, stdout=self.current + '\n')
        assert args[0] == 'default' and args[2] == module.MIME
        self.current = args[1]
        return SimpleNamespace(returncode=0, stdout='')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / 'private home %u'
    home.mkdir()
    launcher = tmp_path / 'trusted studio $;` launcher'
    launcher.write_text('unused test executable')
    launcher.chmod(0o700)
    python = tmp_path / 'Python environment %u' / 'python'
    python.parent.mkdir();python.write_text('unused test executable');python.chmod(0o700)
    script = tmp_path / 'source folder %f $`"\\' / 'receiver_install.py'
    script.parent.mkdir();script.write_text('# trusted test source pathname')
    monkeypatch.setattr(module, '__file__', str(script))
    return home, launcher, python, script, Mime()


def installed(setup, **kwargs):
    home, launcher, python, _, runner = setup
    return module.install('https://trusted.example:8443', launcher, home=home,
                          python_executable=python, mime_runner=runner, **kwargs)


def uri(url='https://trusted.example:8443/handoffs/' + 'a' * 64):
    return 'justbuild://open?handoff=' + quote(url, safe='')


def test_install_pins_local_configuration_and_quotes_exec_without_shell(setup):
    home, launcher, python, script, runner = setup
    result = installed(setup)
    cfg = json.loads(Path(result['config_path']).read_text())
    desktop = Path(result['desktop_path']).read_text()
    assert cfg['allowed_origin'] == 'https://trusted.example:8443'
    assert cfg['launcher'] == str(launcher)
    assert cfg['python_executable'] == str(python)
    assert cfg['handler_script'] == str(script)
    assert cfg['managed_by'] == module.MARKER
    assert Path(result['config_path']).stat().st_mode & 0o777 == 0o600
    exec_line = next(line for line in desktop.splitlines() if line.startswith('Exec='))
    assert exec_line.endswith(' %u')
    assert '%%u' in exec_line and '%%f' in exec_line
    assert '\\\\' in exec_line and '\\$' in exec_line and '\\`' in exec_line
    assert 'sh -c' not in desktop and '--launcher' not in exec_line
    assert not (home / '.cache/justbuild/receiver').exists()
    assert runner.calls == [['query', 'default', module.MIME], ['default', module.DESKTOP, module.MIME],
                            ['query', 'default', module.MIME]]
    validator = shutil.which('desktop-file-validate')
    if validator:
        checked = subprocess.run([validator, result['desktop_path']], capture_output=True, text=True, shell=False)
        assert checked.returncode == 0, checked.stderr + checked.stdout


def test_existing_other_association_requires_explicit_replacement(setup):
    *_, runner = setup
    runner.current = 'other-application.desktop'
    with pytest.raises(module.InstallError, match='Another handler'):
        installed(setup)
    cfg, desktop, _ = module._locations(setup[0])
    assert not cfg.exists() and not desktop.exists()
    result = installed(setup, replace_handler=True)
    assert json.loads(Path(result['config_path']).read_text())['previous_handler'] == 'other-application.desktop'
    module.uninstall(home=setup[0], mime_runner=runner)
    assert runner.current == 'other-application.desktop'


@pytest.mark.parametrize('target', ['config', 'desktop'])
def test_unrelated_files_are_never_overwritten_even_with_replace_flag(setup, target):
    cfg, desktop, _ = module._locations(setup[0])
    path = cfg if target == 'config' else desktop
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('an unrelated file\n')
    before = path.read_bytes()
    with pytest.raises(module.InstallError):
        installed(setup, replace_handler=True)
    assert path.read_bytes() == before


def test_update_owned_install_is_allowed_and_retains_install_identity(setup):
    first = installed(setup)
    ident = json.loads(Path(first['config_path']).read_text())['installation_id']
    second = installed(setup)
    assert json.loads(Path(second['config_path']).read_text())['installation_id'] == ident


def test_handler_calls_only_receive_with_fixed_local_values(setup, monkeypatch):
    result = installed(setup)
    calls = []
    def receiver(url, **kwargs):
        calls.append((url, kwargs));return {'download_status': 'verified', 'print_start': 'not_observed'}
    monkeypatch.setattr(module, 'receive', receiver)
    receipt = module.handle_uri(uri(), Path(result['config_path']))
    assert len(calls) == 1 and receipt['print_start'] == 'not_observed'
    url, args = calls[0]
    assert url == 'https://trusted.example:8443/handoffs/' + 'a' * 64
    assert args['allowed_origin'] == 'https://trusted.example:8443'
    assert args['launcher'] == setup[1]
    assert args['open_studio'] is True and args['allow_loopback_http'] is False
    assert args['cache_root'] == setup[0] / '.cache/justbuild/receiver'


@pytest.mark.parametrize('bad', [
    'justbuild://open?handoff=',
    'justbuild://open?handoff=https://trusted.example:8443/handoffs/' + 'a' * 64,
    uri() + '&launcher=/tmp/evil', uri() + '&handoff=duplicate', uri() + '#fragment',
    uri().replace('justbuild://open', 'justbuild://user@open'),
    uri().replace('justbuild://open', 'justbuild://open/extra'),
    'justbuild://open?handoff=' + quote(quote('https://trusted.example:8443/handoffs/' + 'a' * 64, safe=''), safe=''),
    uri('https://evil.example:8443/handoffs/' + 'a' * 64),
    uri('https://user:pass@trusted.example:8443/handoffs/' + 'a' * 64),
    uri('https://trusted.example:8443/handoffs/' + 'a' * 64 + '?launcher=evil'),
    uri('https://trusted.example:8443/handoffs/' + 'a' * 64 + '#fragment'),
    uri('https://trusted.example:8443/handoffs/' + 'a' * 64 + '\n--launcher=/tmp/x'),
    uri('file:///tmp/evil'), uri('https://trusted.example:8443/handoffs/' + 'a' * 63),
    uri('https://trusted.example:8443/other/' + 'a' * 64),
    'justbuild://open?handoff=%GG', None,
])
def test_protocol_injection_is_rejected_before_receive(setup, monkeypatch, bad):
    result = installed(setup)
    monkeypatch.setattr(module, 'receive', lambda *a, **k: pytest.fail('must not receive'))
    with pytest.raises(module.ReceiverError):
        module.handle_uri(bad, Path(result['config_path']))


def test_loopback_debug_is_explicit_and_restricted_to_literal_loopback(setup):
    url = 'http://127.0.0.1:18766/handoffs/' + 'a' * 64
    with pytest.raises(module.ReceiverError):
        module.parse_uri(uri(url), allowed_origin='http://127.0.0.1:18766')
    assert module.parse_uri(uri(url), allowed_origin='http://127.0.0.1:18766', allow_loopback_http=True) == url
    ipv6 = 'http://[::1]:18766/handoffs/' + 'a' * 64
    assert module.parse_uri(uri(ipv6), allowed_origin='http://[::1]:18766', allow_loopback_http=True) == ipv6
    for host in ('localhost', '127.0.0.2', '192.168.1.4', 'public.example'):
        with pytest.raises(module.ReceiverError):
            module.parse_uri(uri('http://' + host + ':18766/handoffs/' + 'a' * 64),
                             allowed_origin='http://' + host + ':18766', allow_loopback_http=True)


def test_cli_extra_args_and_parse_errors_never_echo_capability_uri(setup, capsys):
    result = installed(setup)
    capability = uri()
    assert module.main(['handle', '--config', result['config_path'], capability, capability]) == 2
    captured = capsys.readouterr()
    assert capability not in captured.out + captured.err and 'a' * 64 not in captured.out + captured.err
    assert module.main(['handle', '--config', result['config_path'], capability, '--launcher', '/tmp/evil']) == 2
    captured = capsys.readouterr()
    assert capability not in captured.out + captured.err
    assert module.main(['handle', '--config', result['config_path'], capability,
                        '--config', '/tmp/alternate-config']) == 2
    captured = capsys.readouterr()
    assert capability not in captured.out + captured.err


def test_uninstall_only_removes_marked_files_and_own_mime_entries_preserves_receipts(setup):
    home, _, _, _, runner = setup
    result = installed(setup)
    cache = home / '.cache/justbuild/receiver/receipts'
    cache.mkdir(parents=True)
    receipt = cache / 'an-existing-receipt.json';receipt.write_text('{"print_start":"not_observed"}')
    cfg, desktop, _ = module._locations(home)
    mime = home / '.config/mimeapps.list'
    mime.write_text('[Default Applications]\n' + module.MIME + '=' + module.DESKTOP + ';other.desktop;\n'
                    'text/plain=editor.desktop;\n[Added Associations]\n' + module.MIME + '=' + module.DESKTOP + ';\n')
    outcome = module.uninstall(home=home, mime_runner=runner)
    assert outcome['receipts_preserved'] is True
    assert not cfg.exists() and not desktop.exists()
    assert receipt.read_text() == '{"print_start":"not_observed"}'
    assert 'text/plain=editor.desktop;' in mime.read_text()
    assert module.MIME + '=other.desktop;' in mime.read_text()
    assert module.DESKTOP not in mime.read_text()


def test_modified_markers_prevent_uninstall_of_unrelated_files(setup):
    result = installed(setup)
    desktop = Path(result['desktop_path']);desktop.write_text(desktop.read_text().replace(module.MARKER, 'some-other-owner'))
    with pytest.raises(module.InstallError):
        module.uninstall(home=setup[0], mime_runner=setup[-1])
    assert desktop.exists() and Path(result['config_path']).exists()


def test_symlink_and_group_writable_config_rejected(setup, tmp_path):
    result = installed(setup)
    cfg = Path(result['config_path']);cfg.chmod(0o666)
    with pytest.raises(module.InstallError):
        module.handle_uri(uri(), cfg)
    cfg.chmod(0o600);saved = cfg.read_bytes();cfg.unlink()
    outside = tmp_path / 'outside';outside.write_bytes(saved);cfg.symlink_to(outside)
    with pytest.raises(module.InstallError):
        module.handle_uri(uri(), cfg)


def test_missing_launcher_and_non_boolean_configuration_rejected(setup, monkeypatch):
    result = installed(setup)
    cfg_path = Path(result['config_path']);cfg = json.loads(cfg_path.read_text())
    cfg['allow_loopback_http'] = 'false';cfg_path.write_text(json.dumps(cfg))
    with pytest.raises(module.InstallError):
        module.handle_uri(uri(), cfg_path)
    cfg['allow_loopback_http'] = False;cfg_path.write_text(json.dumps(cfg));setup[1].unlink()
    with pytest.raises(module.InstallError):
        module.handle_uri(uri(), cfg_path)
