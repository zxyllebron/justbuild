"""Explicit user-level Linux URI receiver installation; never starts a printer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
from urllib.parse import unquote

# Desktop Exec pins this file and the current Python, independent of its cwd.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from justbuild.receiver import ReceiverError, _uri, receive

MARKER = "justbuild-linux-receiver-v1"
DESKTOP = "justbuild-receiver.desktop"
MIME = "x-scheme-handler/justbuild"


class InstallError(ReceiverError):
    pass


def _path(value, *, executable=False, file=False):
    path = Path(value).expanduser().absolute()
    if any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise InstallError("Unsafe local path")
    if file and (not path.is_file() or (executable and not os.access(path, os.X_OK))):
        raise InstallError("A fixed existing local executable is required")
    return path


def _locations(home=None):
    home = _path(home or Path.home())
    # An explicitly supplied home isolates tests from all real XDG state.
    supplied = home != Path.home().absolute()
    config = _path(home / '.config' if supplied else os.environ.get('XDG_CONFIG_HOME', home / '.config'))
    data = _path(home / '.local/share' if supplied else os.environ.get('XDG_DATA_HOME', home / '.local/share'))
    cache = _path(home / '.cache' if supplied else os.environ.get('XDG_CACHE_HOME', home / '.cache'))
    return config / 'justbuild/receiver-client.json', data / 'applications' / DESKTOP, cache / 'justbuild/receiver'


def _read(path, *, limit=65536, private=True):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InstallError("Managed paths must not traverse symbolic links")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or (private and info.st_mode & 0o022) or info.st_size > limit):
            raise InstallError("Unsafe managed file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InstallError("Managed file is too large")
    return data.decode('utf-8')


def _write(path, text, mode, *, private=True, expected=None):
    current = _read(path, private=private)
    if expected is not None and current != expected:
        raise InstallError("Association file changed concurrently")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + secrets.token_hex(16) + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _exec_arg(value):
    # Desktop values are unescaped before Exec quoting is undone. Backslashes
    # introduced for Exec therefore need another escaping layer in the file.
    escaped = ''.join('\\' + c if c in '\\"`$' else c for c in str(value))
    return '"' + escaped.replace('%', '%%').replace('\\', '\\\\') + '"'


def _runner(args, **kwargs):
    binary = shutil.which('xdg-mime')
    if binary is None:
        raise InstallError("xdg-mime is required for explicit protocol registration")
    return subprocess.run([binary, *args], shell=False, capture_output=True, text=True, timeout=10)


def _mime(runner, args):
    try:
        response = runner(args, shell=False, capture_output=True, text=True, timeout=10)
        if response.returncode != 0:
            raise InstallError("MIME registration was not confirmed")
        return response.stdout.strip()
    except InstallError:
        raise
    except Exception:
        raise InstallError("MIME registration was not confirmed") from None


def _owned(config_text, desktop_text):
    if config_text is None and desktop_text is None:
        return None
    try:
        cfg = json.loads(config_text)
        ident = cfg['installation_id']
        if (cfg.get('managed_by') != MARKER or not re.fullmatch('[0-9a-f]{32}', ident)
                or desktop_text is None or f'X-Justbuild-Managed={MARKER}\n' not in desktop_text
                or f'X-Justbuild-Installation={ident}\n' not in desktop_text):
            raise ValueError()
        return cfg
    except (TypeError, ValueError, KeyError):
        raise InstallError("Existing unrelated installation files will not be overwritten") from None


def install(allowed_origin: str, launcher: Path, *, home: Path | None = None,
            python_executable: Path | None = None, allow_loopback_http: bool = False,
            replace_handler: bool = False, mime_runner=None) -> dict:
    """Explicit installation only. No receive, GUI launch or printer operation."""
    if type(allow_loopback_http) is not bool or type(replace_handler) is not bool:
        raise InstallError("Invalid installation option")
    _, _, origin = _uri(allowed_origin, origin_only=True, allow_loopback_http=allow_loopback_http)
    launcher = _path(launcher, executable=True, file=True)
    python = _path(python_executable or sys.executable, executable=True, file=True)
    if '=' in str(python):
        raise InstallError("Desktop executable path cannot contain an equals sign")
    script = _path(__file__, file=True)
    config_path, desktop_path, cache = _locations(home)
    old = _owned(_read(config_path), _read(desktop_path))
    runner = mime_runner or _runner
    current = _mime(runner, ['query', 'default', MIME])
    if current not in ('', DESKTOP) and not replace_handler:
        raise InstallError("Another handler is registered; explicit replacement is required")
    cfg = {'schema_version': '0.1', 'managed_by': MARKER,
           'installation_id': old['installation_id'] if old else secrets.token_hex(16),
           'allowed_origin': origin, 'allow_loopback_http': allow_loopback_http,
           'launcher': str(launcher), 'python_executable': str(python), 'handler_script': str(script),
           'cache_root': str(cache), 'desktop_path': str(desktop_path),
           'previous_handler': old.get('previous_handler', '') if old else current}
    command = ' '.join(_exec_arg(arg) for arg in (python, script, 'handle', '--config', config_path)) + ' %u'
    desktop = ('[Desktop Entry]\nType=Application\nName=justbuild verified file receiver\n'
               'Comment=Import a verified file in Studio; never starts a printer\nNoDisplay=true\nTerminal=false\n'
               f'Exec={command}\nMimeType={MIME};\n'
               f'X-Justbuild-Managed={MARKER}\nX-Justbuild-Installation={cfg["installation_id"]}\n')
    _write(config_path, json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', 0o600)
    _write(desktop_path, desktop, 0o644)
    _mime(runner, ['default', DESKTOP, MIME])
    if _mime(runner, ['query', 'default', MIME]) != DESKTOP:
        raise InstallError("Installation files exist but protocol association was not confirmed")
    return {'status': 'installed', 'config_path': str(config_path), 'desktop_path': str(desktop_path),
            'allowed_origin': origin, 'opens_studio_only': True, 'print_start': 'not_requested'}


def parse_uri(raw: str, *, allowed_origin: str, allow_loopback_http: bool = False) -> str:
    prefix = 'justbuild://open?handoff='
    if (not isinstance(raw, str) or len(raw) > 12288 or not raw.startswith(prefix)
            or not re.fullmatch(r'[A-Za-z0-9._~%\-]+', raw[len(prefix):])
            or re.search(r'%(?![A-Fa-f0-9]{2})', raw)):
        raise InstallError("Invalid single handoff URI")
    try:
        landing = unquote(raw[len(prefix):], encoding='ascii', errors='strict')
    except (UnicodeError, ValueError):
        raise InstallError("Invalid single handoff URI") from None
    parsed, key, _ = _uri(landing, origin_only=False, allow_loopback_http=allow_loopback_http)
    _, allowed_key, _ = _uri(allowed_origin, origin_only=True, allow_loopback_http=allow_loopback_http)
    if key != allowed_key or not re.fullmatch(r'/handoffs/[0-9a-f]{64}', parsed.path):
        raise InstallError("Handoff does not match the fixed trusted service")
    return landing


def handle_uri(raw: str, config_path: Path) -> dict:
    try:
        cfg = json.loads(_read(_path(config_path)))
        if cfg.get('managed_by') != MARKER or type(cfg.get('allow_loopback_http')) is not bool:
            raise ValueError()
        desktop = _read(_path(cfg['desktop_path']))
        _owned(json.dumps(cfg), desktop)
        launcher = _path(cfg['launcher'], file=True, executable=True)
        landing = parse_uri(raw, allowed_origin=cfg['allowed_origin'],
                            allow_loopback_http=cfg['allow_loopback_http'])
        return receive(landing, allowed_origin=cfg['allowed_origin'], cache_root=_path(cfg['cache_root']),
                       open_studio=True, launcher=launcher, allow_loopback_http=cfg['allow_loopback_http'])
    except ReceiverError:
        raise
    except (ValueError, TypeError, KeyError, OSError):
        raise InstallError("Invalid fixed receiver configuration") from None


def _remove_own_mime_entries(paths):
    for path in paths:
        # MIME registries are shared desktop preferences, not receiver secrets;
        # preserve their existing mode and edit only our exact application ID.
        content = _read(path, private=False)
        if content is None:
            continue
        section, lines = '', []
        for line in content.splitlines(keepends=True):
            if line.lstrip().startswith('['):
                section = line.strip()
            if (section in ('[Default Applications]', '[Added Associations]', '[Removed Associations]')
                    and line.split('=', 1)[0].strip() == MIME and '=' in line):
                key, values = line.rstrip('\r\n').split('=', 1)
                entries = values.split(';')
                if DESKTOP in [e.strip() for e in entries]:
                    remaining = [e for e in entries if e.strip() != DESKTOP]
                    if any(e.strip() for e in remaining):
                        lines.append(key + '=' + ';'.join(remaining) + '\n')
                    continue
            lines.append(line)
        updated = ''.join(lines)
        if updated != content:
            _write(path, updated, stat.S_IMODE(path.stat().st_mode), private=False, expected=content)


def uninstall(*, home: Path | None = None, mime_runner=None) -> dict:
    config_path, desktop_path, cache = _locations(home)
    cfg = _owned(_read(config_path), _read(desktop_path))
    if cfg is None:
        return {'status': 'not_installed', 'receipts_preserved': True}
    current = _mime(mime_runner or _runner, ['query', 'default', MIME])
    previous = cfg.get('previous_handler', '')
    if current == DESKTOP and previous and previous != DESKTOP:
        if not re.fullmatch(r'[A-Za-z0-9._-]+\.desktop', previous):
            raise InstallError("Saved previous association is invalid")
        _mime(mime_runner or _runner, ['default', previous, MIME])
    candidates = [config_path.parent.parent / 'mimeapps.list', desktop_path.parent / 'mimeapps.list']
    desktop_names = re.split(r'[:;]', os.environ.get('XDG_CURRENT_DESKTOP', ''))
    for name in desktop_names:
        if re.fullmatch('[A-Za-z0-9_-]+', name):
            candidates.append(config_path.parent.parent / (name.lower() + '-mimeapps.list'))
    _remove_own_mime_entries(candidates)
    # Only the exact paired, marked files are removed. Cache/receipts are untouched.
    desktop_path.unlink(); config_path.unlink()
    return {'status': 'uninstalled', 'receipts_preserved': True, 'print_start': 'not_requested'}


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes unknown argv, potentially a bearer URL.
        raise InstallError("Invalid command arguments")


def main(argv=None) -> int:
    parser = _SafeParser(description='Explicit Linux file-receiver protocol installation; no printer control')
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('install'); add.add_argument('--allowed-origin', required=True)
    add.add_argument('--launcher', type=Path, required=True)
    add.add_argument('--allow-loopback-http', action='store_true');add.add_argument('--replace-handler', action='store_true')
    sub.add_parser('uninstall')
    handle = sub.add_parser('handle');handle.add_argument('--config', type=Path, required=True);handle.add_argument('uri')
    try:
        supplied = list(sys.argv[1:] if argv is None else argv)
        if supplied and supplied[0] == 'handle' and (len(supplied) != 4 or supplied[1] != '--config'):
            raise InstallError("The handler accepts exactly one URI and its fixed configuration")
        args = parser.parse_args(supplied)
        if args.command == 'install':
            value = install(args.allowed_origin, args.launcher, allow_loopback_http=args.allow_loopback_http,
                            replace_handler=args.replace_handler)
        elif args.command == 'uninstall':
            value = uninstall()
        else:
            value = handle_uri(args.uri, args.config)
        print(json.dumps(value, ensure_ascii=False));return 0
    except (ReceiverError, OSError, ValueError, RuntimeError):
        print('Receiver operation was not completed; inspect local configuration and receipts. No printer start was requested.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
