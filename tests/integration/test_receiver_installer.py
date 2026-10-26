"""Package review on Linux; compilation/argv checks require real Windows .NET.

Windows checks use -PrepareOnly, never register protocols or start a receiver.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-windows-receiver.ps1"
DOC = ROOT / "docs" / "windows-receiver-install.md"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def test_protocol_registration_targets_native_argv_guard_not_powershell():
    text = INSTALLER.read_text()
    assert "HKCU:\\Software\\Classes\\justbuild" in text
    assert "$command = '\"' + $launcher + '\" \"%1\"'" in text
    assert 'if (args == null || args.Length != 1) return 2;' in text
    assert text.index('if (args == null || args.Length != 1) return 2;') < text.index('Process.Start(start)')
    for forbidden in ("Invoke-Expression", "-EncodedCommand", "-ExecutionPolicy", "Register-ScheduledTask",
                      "New-Service", "New-NetFirewallRule", "HKLM:\\"):
        assert forbidden not in text
    assert "start.UseShellExecute = false;" in text
    assert "argumentList.GetValue" in text
    assert "start.Arguments = String.Join" in text


def test_installer_limits_origin_and_preserves_unrelated_handlers():
    text = INSTALLER.read_text()
    assert "AllowedOrigin" in text and "landing.IdnHost, allowed.IdnHost" in text
    assert "landing.Port != allowed.Port" in text
    assert "Uri.UnescapeDataString(encoded); // Exactly once" in text
    assert "Assert-OwnedHandler $command $install" in text
    assert "Assert-OwnedDirectory $install" in text
    assert "JustbuildOwner" in text and "install-owner.json" in text
    assert "ReparsePoint" in text
    assert "inbox_and_receipts_preserved=$true" in text
    assert "$files = @('ProtocolLauncher.exe', 'windows-receiver.ps1', 'protocol-config.json', 'install-owner.json')" in text
    assert "-PrepareOnly" in text and "if (-not $PrepareOnly)" in text


def test_install_document_keeps_windows_and_print_evidence_separate():
    text = DOC.read_text()
    for required in ("一次", "AllowedOrigin", "Uninstall", "Windows 实机", "未验证", "不启动打印", "HKCU"):
        assert required in text


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    if SHELL is None:
        pytest.skip("Requires Windows PowerShell 5.1 and .NET compiler; Linux cannot validate Windows installation")
    folder = tmp_path_factory.mktemp("protocol-native")
    env = dict(os.environ, LOCALAPPDATA=str(folder))
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(INSTALLER),
                             "-AllowedOrigin", "https://trusted.example:8443", "-PrepareOnly"],
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["registered"] is False
    assert info["allowed_origin"] == "https://trusted.example:8443"
    assert Path(info["launcher_path"]).is_file()
    return folder, info


def test_windows_compile_and_pure_uri_validation(prepared):
    folder, info = prepared
    origin = "https://trusted.example:8443"
    landing = origin + "/handoffs/" + "a" * 64
    custom = "justbuild://open?handoff=" + quote(landing, safe="")
    cases = [{"args": [custom], "origin": origin, "expected": True}]
    bad_urls = [
        "https://evil.example:8443/handoffs/" + "a" * 64,
        "https://trusted.example/handoffs/" + "a" * 64,
        "http://trusted.example:8443/handoffs/" + "a" * 64,
        "https://user:pass@trusted.example:8443/handoffs/" + "a" * 64,
        landing + "?command=calc.exe", landing + "#fragment", landing + '" -Command calc.exe',
        landing + "\r\n", origin + "/handoffs/../secrets", landing + "%22",
    ]
    cases.extend({"args": ["justbuild://open?handoff=" + quote(url, safe="")],
                  "origin": origin, "expected": False} for url in bad_urls)
    cases.extend([
        {"args": [custom, "-Command", "calc.exe"], "origin": origin, "expected": False},
        {"args": [custom + '" -Command calc.exe "'], "origin": origin, "expected": False},
        {"args": [], "origin": origin, "expected": False},
        {"args": ["justbuild://open?handoff=" + quote(quote(landing, safe=""), safe="")],
         "origin": origin, "expected": False},
    ])
    inputs = folder / "cases.json"
    inputs.write_text(json.dumps(cases), encoding="utf-8")
    probe = folder / "validate.ps1"
    probe.write_text(r'''
param([string]$Executable,[string]$Cases)
$ErrorActionPreference='Stop'
$type=[Reflection.Assembly]::LoadFile($Executable).GetType('ProtocolLauncher')
$method=$type.GetMethod('ValidateArguments')
$results=@()
foreach($case in (Get-Content -LiteralPath $Cases -Raw | ConvertFrom-Json)) {
    $arguments=New-Object object[] 2
    $arguments[0]=[string[]]$case.args
    $arguments[1]=[string]$case.origin
    try {$null=$method.Invoke($null,$arguments);$accepted=$true} catch {$accepted=$false}
    $results+=($accepted -eq [bool]$case.expected)
}
ConvertTo-Json -InputObject $results -Compress
''', encoding="utf-8")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(probe),
                             "-Executable", info["launcher_path"], "-Cases", str(inputs)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout))


def test_windows_native_quote_injection_is_rejected_before_any_receiver(prepared):
    folder, info = prepared
    probe = folder / "argv.ps1"
    probe.write_text(r'''
param([string]$Executable)
$ErrorActionPreference='Stop'
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName=$Executable
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
# The malformed %1 splits into extra native argv, never PowerShell arguments.
$start.Arguments='"justbuild://open?handoff=https%3A%2F%2Ftrusted.example%3A8443%2Fhandoffs%2F' + ('a'*64) + '" -Command calc.exe ""'
$child=[Diagnostics.Process]::Start($start)
if(-not $child.WaitForExit(10000)){throw 'Argument guard did not exit.'}
if($child.ExitCode -ne 2){throw 'Argument guard did not reject extra argv.'}
if(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'justbuild\receipts')){throw 'Unexpected receiver execution.'}
$child.Dispose()
Write-Output 'guard_rejected'
''', encoding="utf-8")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(probe),
                             "-Executable", info["launcher_path"]],
                            env=dict(os.environ, LOCALAPPDATA=str(folder)),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "guard_rejected" in result.stdout
