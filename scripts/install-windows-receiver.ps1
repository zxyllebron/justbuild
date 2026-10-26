#requires -Version 5.1
<#
.SYNOPSIS
Installs a current-user justbuild:// receiver for one trusted HTTPS origin.
.DESCRIPTION
Run deliberately once on Windows after reviewing this script and its sibling
windows-receiver.ps1. No printer command, service, polling or policy change.
#>
[CmdletBinding()]
param(
    [string]$AllowedOrigin,
    [switch]$PrepareOnly,
    [switch]$Uninstall
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$owner = 'justbuild.protocol.receiver.v1'
$keyPath = 'HKCU:\Software\Classes\justbuild'
$files = @('ProtocolLauncher.exe', 'windows-receiver.ps1', 'protocol-config.json', 'install-owner.json')

function Assert-NoReparse([string]$Path) {
    $itemPath = [IO.Path]::GetFullPath($Path)
    while ($itemPath) {
        if (Test-Path -LiteralPath $itemPath) {
            if (((Get-Item -LiteralPath $itemPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Refusing a reparse-point installation path.'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($itemPath)
        if ($parent -eq $itemPath) { break }
        $itemPath = $parent
    }
}

function Assert-OwnedDirectory([string]$Path) {
    Assert-NoReparse $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $marker = Join-Path $Path 'install-owner.json'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'Existing installation directory is not owned by this installer.' }
    $saved = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($saved.owner -cne $owner -or $saved.install_directory -ine $Path) {
        throw 'Existing installation directory has a different owner.'
    }
    foreach ($name in $files) { Assert-NoReparse (Join-Path $Path $name) }
}

function Get-TrustedOrigin([string]$Value) {
    if (-not $Value -or $Value.Length -gt 2048 -or $Value -cmatch '[^\x21-\x7e]|["\\%]') { throw 'A plain HTTPS origin is required.' }
    $uri = [Uri]$Value
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or
        $uri.AbsolutePath -ne '/' -or $uri.Port -le 0) { throw 'AllowedOrigin must contain HTTPS, host and optional port only.' }
    return $uri.GetLeftPart([UriPartial]::Authority).TrimEnd('/')
}

function Assert-OwnedHandler([string]$ExpectedCommand, [string]$InstallDirectory) {
    if (-not (Test-Path -LiteralPath $keyPath)) { return }
    $key = Get-Item -LiteralPath $keyPath
    $commandKey = Join-Path $keyPath 'shell\open\command'
    if ($key.GetValue('JustbuildOwner') -cne $owner -or -not (Test-Path -LiteralPath $commandKey) -or
        (Get-Item -LiteralPath $commandKey).GetValue('') -cne $ExpectedCommand) {
        throw 'The justbuild protocol belongs to another handler; it was not changed.'
    }
    if (-not (Test-Path -LiteralPath $InstallDirectory -PathType Container)) { throw 'Existing protocol lacks its owned installation directory.' }
    Assert-OwnedDirectory $InstallDirectory
}

if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSEdition -ne 'Desktop') { throw 'Run with Windows PowerShell 5.1 on Windows.' }
if (-not $env:LOCALAPPDATA -or $env:LOCALAPPDATA -notmatch '^[A-Za-z]:\\') { throw 'A local current-user LOCALAPPDATA directory is required.' }
if ($Uninstall -and $PrepareOnly) { throw '-Uninstall and -PrepareOnly cannot be combined.' }
$base = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'justbuild'))
$install = Join-Path $base 'protocol'
$launcher = Join-Path $install 'ProtocolLauncher.exe'
# REG_SZ, with no shell or PowerShell in the registered command. Extra arguments
# introduced by malformed raw quotes are rejected in native Main before launch.
$command = '"' + $launcher + '" "%1"'
Assert-NoReparse $base

if ($Uninstall) {
    Assert-OwnedHandler $command $install
    Assert-OwnedDirectory $install
    if (Test-Path -LiteralPath $keyPath) { Remove-Item -LiteralPath $keyPath -Recurse -Force }
    if (Test-Path -LiteralPath $install) {
        foreach ($name in $files) { Remove-Item -LiteralPath (Join-Path $install $name) -Force -ErrorAction SilentlyContinue }
        if (@(Get-ChildItem -LiteralPath $install -Force).Count -eq 0) { Remove-Item -LiteralPath $install }
    }
    [ordered]@{ schema_version='0.1'; uninstalled=$true; inbox_and_receipts_preserved=$true } | ConvertTo-Json
    exit 0
}

$trusted = Get-TrustedOrigin $AllowedOrigin
$receiver = Join-Path $PSScriptRoot 'windows-receiver.ps1'
if (-not (Test-Path -LiteralPath $receiver -PathType Leaf)) { throw 'Keep the reviewed windows-receiver.ps1 beside this installer.' }
Assert-NoReparse $receiver
if ($PrepareOnly) { $install = Join-Path $base 'protocol-preview' }
else { Assert-OwnedHandler $command $install }
Assert-OwnedDirectory $install

# Compiled with Windows' existing .NET compiler through Windows PowerShell Add-Type.
# This is a native argv boundary, rather than passing untrusted %1 to powershell.
$source = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public static class ProtocolLauncher {
    private const string Owner = "justbuild.protocol.receiver.v1";

    public static string Validate(string raw, string origin) {
        const string prefix = "justbuild://open?handoff=";
        if (raw == null || raw.Length > 8192 || !raw.StartsWith(prefix, StringComparison.Ordinal)) throw new FormatException();
        string encoded = raw.Substring(prefix.Length);
        if (!Regex.IsMatch(encoded, @"\A(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+\z")) throw new FormatException();
        string decoded = Uri.UnescapeDataString(encoded); // Exactly once; never execute decoded text.
        foreach (char c in decoded) if (c <= 32 || c >= 127 || c == '"' || c == '\\') throw new FormatException();
        Uri landing, allowed;
        if (!Uri.TryCreate(decoded, UriKind.Absolute, out landing) ||
            !Uri.TryCreate(origin, UriKind.Absolute, out allowed)) throw new FormatException();
        if (allowed.Scheme != "https" || allowed.UserInfo.Length != 0 || allowed.Query.Length != 0 ||
            allowed.Fragment.Length != 0 || allowed.AbsolutePath != "/") throw new FormatException();
        if (landing.Scheme != "https" || landing.UserInfo.Length != 0 || landing.Query.Length != 0 ||
            landing.Fragment.Length != 0 || !Regex.IsMatch(landing.AbsolutePath, @"\A/handoffs/[0-9a-f]{64}\z") ||
            !Regex.IsMatch(decoded, @"\Ahttps://[^/?#]+/handoffs/[0-9a-f]{64}\z") ||
            !String.Equals(landing.IdnHost, allowed.IdnHost, StringComparison.OrdinalIgnoreCase) ||
            landing.Port != allowed.Port) throw new FormatException();
        return landing.AbsoluteUri;
    }

    public static string QuoteArgument(string value) {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append('"'); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    private static string Sha256(string path) {
        using (SHA256 hash = SHA256.Create())
        using (FileStream input = File.OpenRead(path))
            return BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
    }

    // Public pure check is also exercised by Windows reflection tests.
    public static string ValidateArguments(string[] args, string origin) {
        if (args == null || args.Length != 1) throw new FormatException();
        return Validate(args[0], origin);
    }

    [STAThread]
    public static int Main(string[] args) {
        try {
            // Reject quote-injected argv before reading any configuration or starting PowerShell.
            if (args == null || args.Length != 1) return 2;
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string configPath = Path.Combine(root, "protocol-config.json");
            if (new FileInfo(configPath).Length > 16384) return 2;
            Dictionary<string, object> config = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(configPath));
            if ((string)config["owner"] != Owner) return 2;
            string url = ValidateArguments(args, (string)config["allowed_origin"]);
            string script = Path.Combine(root, "windows-receiver.ps1");
            if (new FileInfo(script).Length > 1048576 || Sha256(script) != (string)config["receiver_sha256"]) return 2;
            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            ProcessStartInfo start = new ProcessStartInfo(powershell);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WorkingDirectory = root;
            string[] values = { "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script, "-HandoffUrl", url };
            var argumentList = typeof(ProcessStartInfo).GetProperty("ArgumentList");
            if (argumentList != null) {
                var list = argumentList.GetValue(start, null) as IList<string>;
                if (list == null) return 2;
                foreach (string value in values) list.Add(value);
            } else {
                string[] quoted = Array.ConvertAll(values, QuoteArgument);
                start.Arguments = String.Join(" ", quoted);
            }
            using (Process child = Process.Start(start)) {
                if (child == null) return 3;
                // Download/open evidence is written by the existing receiver, not this launcher.
                child.WaitForExit();
                if (child.ExitCode != 0) throw new InvalidOperationException();
            }
            return 0;
        } catch {
            MessageBox.Show("justbuild receiver could not complete. Check the trusted origin, link expiry, script policy and installed Bambu Studio. No print was started by this launcher.", "justbuild receiver", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 2;
        }
    }
}
'@

[void][IO.Directory]::CreateDirectory($base)
$staging = Join-Path $base ('.protocol-build-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($staging)
try {
    $built = Join-Path $staging 'ProtocolLauncher.exe'
    Add-Type -TypeDefinition $source -Language CSharp -ReferencedAssemblies @('System.dll', 'System.Core.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll') -OutputAssembly $built -OutputType WindowsApplication
    Copy-Item -LiteralPath $receiver -Destination (Join-Path $staging 'windows-receiver.ps1')
    $hash = (Get-FileHash -LiteralPath (Join-Path $staging 'windows-receiver.ps1') -Algorithm SHA256).Hash.ToLowerInvariant()
    $config = [ordered]@{ schema_version='0.1'; owner=$owner; allowed_origin=$trusted; receiver_sha256=$hash }
    $marker = [ordered]@{ schema_version='0.1'; owner=$owner; install_directory=$install }
    [IO.File]::WriteAllText((Join-Path $staging 'protocol-config.json'), ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $staging 'install-owner.json'), ($marker | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    [void][IO.Directory]::CreateDirectory($install)
    foreach ($name in $files) {
        $dest = Join-Path $install $name
        if ([IO.File]::Exists($dest)) { [IO.File]::Replace((Join-Path $staging $name), $dest, $null) }
        else { [IO.File]::Move((Join-Path $staging $name), $dest) }
    }
    if (-not $PrepareOnly) {
        # Recheck immediately before claiming the per-user protocol.
        Assert-OwnedHandler $command $install
        [void](New-Item -Path $keyPath -Force)
        $key = Get-Item -LiteralPath $keyPath
        $key.SetValue('', 'URL:justbuild receiver', [Microsoft.Win32.RegistryValueKind]::String)
        $key.SetValue('URL Protocol', '', [Microsoft.Win32.RegistryValueKind]::String)
        $key.SetValue('JustbuildOwner', $owner, [Microsoft.Win32.RegistryValueKind]::String)
        $open = Join-Path $keyPath 'shell\open\command'
        [void](New-Item -Path $open -Force)
        (Get-Item -LiteralPath $open).SetValue('', $command, [Microsoft.Win32.RegistryValueKind]::String)
    }
    [ordered]@{ schema_version='0.1'; registered=(-not $PrepareOnly); allowed_origin=$trusted;
        install_directory=$install; launcher_path=(Join-Path $install 'ProtocolLauncher.exe');
        receiver_sha256=$hash; printer_receipt='not_observed'; print_start='not_observed' } | ConvertTo-Json
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
