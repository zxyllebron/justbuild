# Linux user-level link receiver installation

Implemented in `src/justbuild/receiver_install.py`. This is the explicit one-time client setup for a
`justbuild://open?handoff=...` link. It downloads through the existing receiver, verifies bytes/SHA-256,
and requests file import in the locally configured Bambu Studio launcher. **It does not start a print,
contact a printer, verify a Studio import, or establish cloud print dispatch.**

No actual installation, MIME registration, GUI launch or printer action was executed while implementing
this module. All installer tests use a private temporary home and a mock MIME command runner.

## Explicit install and removal

Run using the Python environment that already provides justbuild and its receiver dependencies:

```bash
/absolute/path/to/python -m justbuild.receiver_install install \
  --allowed-origin https://your-fixed-service.example \
  --launcher /absolute/path/to/bambu-studio
```

The fixed HTTPS origin and existing local executable are explicitly selected during installation. The
desktop entry pins that same Python executable and the absolute local handler script, so it works from
an unrelated current directory and when the source path contains spaces. Python symlinks are preserved
instead of resolving away a virtual environment. No program path or command argument comes from the
remote handoff manifest or URI.

The installer writes only the user's managed files:

- `$XDG_CONFIG_HOME/justbuild/receiver-client.json` (default `~/.config/...`), mode 0600.
- `$XDG_DATA_HOME/applications/justbuild-receiver.desktop` (default `~/.local/share/...`), mode 0644.
- The explicit `xdg-mime default justbuild-receiver.desktop x-scheme-handler/justbuild` registration.

Both files carry a shared installation identifier and an ownership marker. Existing unrelated files
at those paths are never overwritten. If another application already owns the protocol, the default
behavior refuses replacement. `--replace-handler` is an explicit optional replacement; its previous
association is recorded for removal. The installer reads the association back before reporting success.
If association confirmation fails, the managed files remain inspectable and a later explicit install
can reconcile them. No background service or listening port is installed.

Uninstall explicitly:

```bash
/absolute/path/to/python -m justbuild.receiver_install uninstall
```

Removal requires the exact matching ownership markers. It removes those two managed files and only
this application's exact ID from relevant user MIME preference entries; unrelated applications, MIME
keys and comments remain. A previously replaced handler is restored only if justbuild still owns the
current association. **Receiver inbox files and receipts are never deleted.** Symlink traversal,
different file owners and unsafe private configuration permissions are rejected.

## Link and handler contract

The website builds its launch link from an existing scoped HTTPS landing URL:

```python
from urllib.parse import quote
launch_uri = "justbuild://open?handoff=" + quote(https_landing_url, safe="")
```

The desktop entry supplies exactly one unquoted `%u` field, after the fixed Python/script/configuration
arguments. The handler accepts exactly `handle --config FIXED_CONFIG SINGLE_URI`, percent-decodes the
handoff once, and requires its normalized origin to equal the configured allowed origin. Its only
accepted landing path is `/handoffs/` plus 64 lowercase hex characters. Duplicate handoff/configuration
arguments, extra arguments, raw nested URL delimiters, credentials, fragments, nested queries, invalid
escapes, double encoding, foreign origins and nonhandoff paths are rejected before network reception.

The sole receive call uses the pinned `allowed_origin`, `cache_root` and local `launcher`, with
`open_studio=True`. The underlying receiver continues to reject redirects, validate the exact file hash
and size, record durable token-free receipts, and avoid duplicate launches. Installer/handler errors are
generic and do not echo argument values or bearer URLs; argparse's usual unknown-argument echo is
deliberately disabled.

The explicit `--allow-loopback-http` installation flag exists only for local testing. HTTP is then
accepted only on literal `127.0.0.1` or `::1`; `localhost`, other loopback aliases, LAN addresses and public
hosts still require HTTPS. This exception is never remotely selected by a link.

Browsers may still ask permission to open a registered external protocol. The receiver does not bypass
that browser prompt, the Studio UI or the user's explicit print review. A `process_started` receipt
means a local process was requested; Studio import, printer receipt and print start remain separately
unobserved until independently checked.

## Desktop escaping sources and checks

The implementation follows the [freedesktop Desktop Entry Exec specification](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html)
and [string value escaping rules](https://specifications.freedesktop.org/desktop-entry/latest/value-types.html):
fixed arguments are double-quoted, Exec-special characters and the desktop string's backslashes receive
their separate escaping layers, literal percentages become `%%`, and the single URL field `%u` remains
outside quoted arguments. No shell is invoked.

Test command:

```bash
/home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_linux_receiver_install.py -q
```

Result: **30 passed in 0.43 s**. Tests cover fixed executable/origin configuration, current-Python binding,
source/home paths containing spaces and literal field codes, ownership/conflict handling, update/removal,
receipt preservation, URI and CLI injection, duplicate configuration arguments, single decoding, strict
loopback options, unsafe config permissions and symlinks. The installed `desktop-file-validate` utility
also validated the generated temporary desktop entry, including special-character paths. MIME operations
were mocked; no GUI or printer was launched, and no global or real user association changed.
