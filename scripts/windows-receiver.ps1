#requires -Version 5.1
<#
.SYNOPSIS
Receives one reviewed justbuild 3MF and opens it in an installed Bambu Studio.
.DESCRIPTION
No installation, protocol registration, polling, or printer commands are performed.
Download/open receipts are separate from printer receipt or print-start evidence.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias('handoff-url', '-handoff-url')]
    [string]$HandoffUrl,
    [string]$BambuStudioPath,
    [switch]$AllowLoopbackHttp,
    [switch]$DownloadOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-TransportUri([Uri]$Uri) {
    if (-not $Uri.IsAbsoluteUri -or $Uri.UserInfo -or $Uri.Fragment) {
        throw 'The handoff and download URLs must be absolute and contain no user info or fragment.'
    }
    $loopback = $Uri.Host -in @('localhost', '127.0.0.1', '[::1]', '::1')
    if ($Uri.Scheme -ne 'https' -and -not ($AllowLoopbackHttp -and $loopback -and $Uri.Scheme -eq 'http')) {
        throw 'HTTPS is required; -AllowLoopbackHttp is only for explicit local development.'
    }
}

function Test-SameOrigin([Uri]$A, [Uri]$B) {
    return $A.Scheme -eq $B.Scheme -and $A.IdnHost -eq $B.IdnHost -and $A.Port -eq $B.Port
}

function Write-Receipt([string]$Path, $Value) {
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($Path)) {
            # Same-volume atomic replacement: do not lose the pre-launch receipt on interruption.
            [IO.File]::Replace($temp, $Path, $null)
        } else {
            [IO.File]::Move($temp, $Path)
        }
    } finally {
        if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
    }
}

function Read-LimitedBody($Response, [long]$Limit, [string]$Destination) {
    if ($Response.Content.Headers.ContentLength -and $Response.Content.Headers.ContentLength -gt $Limit) {
        throw 'Response exceeds its allowed size.'
    }
    $source = $Response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $target = $null
    try {
        if ($Destination) {
            $target = [IO.FileStream]::new($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        } else {
            $target = [IO.MemoryStream]::new()
        }
        $buffer = New-Object byte[] 65536
        [long]$total = 0
        $clock = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $remaining = 120000 - [int]$clock.ElapsedMilliseconds
            if ($remaining -le 0) { throw 'Response body timed out.' }
            $read = $source.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $read.Wait($remaining)) { throw 'Response body timed out.' }
            $count = $read.GetAwaiter().GetResult()
            if ($count -eq 0) { break }
            $total += $count
            if ($total -gt $Limit) { throw 'Response exceeds its allowed size.' }
            $target.Write($buffer, 0, $count)
        }
        if ($Destination) { $target.Flush($true); return $total }
        return [Text.Encoding]::UTF8.GetString($target.ToArray())
    } finally {
        $source.Dispose()
        if ($target) { $target.Dispose() }
    }
}

function Get-CheckedResponse($Client, [Uri]$Uri) {
    $response = $Client.GetAsync($Uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    if ([int]$response.StatusCode -ne 200) {
        $code = [int]$response.StatusCode
        $response.Dispose()
        throw "The trusted service returned HTTP $code; redirects are not followed."
    }
    return $response
}

function Get-InstalledStudio([string]$Explicit, [string]$ConfigPath) {
    $candidates = [Collections.Generic.List[string]]::new()
    if ($Explicit) {
        $candidates.Add($Explicit)
    } elseif (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        $saved = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        if ($saved.bambu_studio_path) { $candidates.Add([string]$saved.bambu_studio_path) }
    }
    if (-not $Explicit) {
        foreach ($key in @(
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\bambu-studio.exe',
            'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\bambu-studio.exe',
            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\bambu-studio.exe'
        )) {
            if (Test-Path -LiteralPath $key) {
                $value = (Get-Item -LiteralPath $key).GetValue('')
                if ($value) { $candidates.Add([string]$value) }
            }
        }
        foreach ($root in @(
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
            'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
        )) {
            if (Test-Path -LiteralPath $root) {
                foreach ($entry in @(Get-ChildItem -LiteralPath $root)) {
                    if ($entry.GetValue('DisplayName') -like 'Bambu Studio*') {
                        $location = $entry.GetValue('InstallLocation')
                        if ($location) { $candidates.Add((Join-Path $location 'bambu-studio.exe')) }
                    }
                }
            }
        }
        foreach ($folder in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
            if ($folder) {
                $candidates.Add((Join-Path $folder 'Bambu Studio\bambu-studio.exe'))
                $candidates.Add((Join-Path $folder 'BambuStudio\bambu-studio.exe'))
                $candidates.Add((Join-Path $folder 'Programs\Bambu Studio\bambu-studio.exe'))
            }
        }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -match '^[A-Za-z]:\\' -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $resolved = (Resolve-Path -LiteralPath $candidate).ProviderPath
            if ([IO.Path]::GetFileName($resolved) -in @('bambu-studio.exe', 'BambuStudio.exe')) { return $resolved }
        }
    }
    throw 'Installed Bambu Studio was not found. Retry once with -BambuStudioPath pointing to its existing executable.'
}

function ConvertTo-WindowsArgument([string]$Value) {
    # CommandLineToArgvW/MSVC-compatible quoting for Windows PowerShell 5.1.
    # Always pass one literal filename; no shell or file association is involved.
    $quoted = [Text.StringBuilder]::new()
    [void]$quoted.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$quoted.Append(('\' * (2 * $slashes + 1)))
            [void]$quoted.Append('"')
        } else {
            [void]$quoted.Append(('\' * $slashes))
            [void]$quoted.Append($character)
        }
        $slashes = 0
    }
    [void]$quoted.Append(('\' * (2 * $slashes)))
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

$client = $null
$handler = $null
$lock = $null
$temporary = $null
$response = $null
try {
    if ($env:OS -ne 'Windows_NT' -or -not $env:LOCALAPPDATA) { throw 'Run this receiver on Windows.' }
    Add-Type -AssemblyName System.Net.Http
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    [Uri]$landing = $HandoffUrl
    Assert-TransportUri $landing
    $builder = [UriBuilder]::new($landing)
    $builder.Path = $builder.Path.TrimEnd('/') + '/manifest'
    [Uri]$manifestUri = $builder.Uri
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseDefaultCredentials = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(120)
    $response = Get-CheckedResponse $client $manifestUri
    try { $rawManifest = Read-LimitedBody $response 65536 $null } finally { $response.Dispose(); $response = $null }
    $manifest = $rawManifest | ConvertFrom-Json
    foreach ($name in @('id', 'download_url', 'sha256', 'bytes', 'basename')) {
        if ($manifest.PSObject.Properties.Name -notcontains $name) { throw "Manifest field missing: $name" }
    }
    if ($manifest.id -isnot [string] -or $manifest.id -cnotmatch '^[A-Za-z0-9_-]{1,128}$') { throw 'Invalid handoff id.' }
    if ($manifest.sha256 -isnot [string] -or $manifest.sha256 -cnotmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid SHA-256.' }
    if (($manifest.bytes -isnot [int] -and $manifest.bytes -isnot [long]) -or $manifest.bytes -le 0 -or $manifest.bytes -gt 1073741824) {
        throw 'Invalid file size; the receiver accepts files up to 1 GiB.'
    }
    if ($manifest.basename -isnot [string] -or $manifest.basename -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.3mf$' -or
        $manifest.basename.Split('.')[0] -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { throw 'Unsafe 3MF basename.' }
    if ($manifest.download_url -isnot [string]) { throw 'Invalid download URL.' }
    [Uri]$downloadUri = [Uri]::new($manifestUri, $manifest.download_url)
    Assert-TransportUri $downloadUri
    if (-not (Test-SameOrigin $landing $downloadUri)) { throw 'The download URL must use the same origin as the handoff.' }

    $root = Join-Path $env:LOCALAPPDATA 'justbuild'
    $inbox = Join-Path (Join-Path $root 'inbox') $manifest.id
    $states = Join-Path $root 'receipts'
    [void][IO.Directory]::CreateDirectory($inbox)
    [void][IO.Directory]::CreateDirectory($states)
    $receiptPath = Join-Path $states ($manifest.id + '.json')
    $lockPath = Join-Path $states ($manifest.id + '.lock')
    try { $lock = [IO.FileStream]::new($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw 'This handoff is already being handled by another receiver process.' }
    $destination = Join-Path $inbox $manifest.basename
    $origin = $landing.GetLeftPart([UriPartial]::Authority)
    $receipt = [ordered]@{
        schema_version = '0.1'; id = $manifest.id; origin = $origin; sha256 = $manifest.sha256.ToLowerInvariant()
        bytes = [long]$manifest.bytes; basename = $manifest.basename; local_path = $destination
        download_status = 'pending'; open_status = 'pending'; printer_receipt = 'not_observed'; print_start = 'not_observed'
        updated_at = [DateTime]::UtcNow.ToString('o')
    }
    if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
        $previous = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
        foreach ($field in @('id', 'origin', 'sha256', 'bytes', 'basename', 'local_path')) {
            if ($previous.$field -cne $receipt[$field]) { throw 'Handoff id conflicts with its existing receipt; no file or process was replaced.' }
        }
        foreach ($field in @('download_status', 'open_status')) { $receipt[$field] = $previous.$field }
    }
    if ([IO.File]::Exists($destination)) {
        if ((Get-Item -LiteralPath $destination).Length -ne $manifest.bytes -or
            (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ine $manifest.sha256) { throw 'Existing inbox file failed integrity validation.' }
    } else {
        if ($receipt.download_status -eq 'verified') { throw 'Previously verified local file is missing; manual reconciliation is required.' }
        $temporary = $destination + '.' + [Guid]::NewGuid().ToString('N') + '.partial'
        $response = Get-CheckedResponse $client $downloadUri
        try { $received = Read-LimitedBody $response ([long]$manifest.bytes) $temporary } finally { $response.Dispose(); $response = $null }
        if ($received -ne $manifest.bytes -or (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ine $manifest.sha256) {
            throw 'Downloaded file failed size or SHA-256 validation.'
        }
        [IO.File]::Move($temporary, $destination)
        $temporary = $null
    }
    $receipt.download_status = 'verified'
    $receipt.updated_at = [DateTime]::UtcNow.ToString('o')
    Write-Receipt $receiptPath $receipt
    if ($receipt.open_status -in @('launch_unknown', 'process_started')) {
        # Persisted launch_unknown includes a crash between Process.Start and receipt update.
        # Reconciliation must happen before any repeat; never blindly open it twice.
        $receipt | ConvertTo-Json -Depth 8
        exit 0
    }
    if ($receipt.open_status -ne 'pending') { throw 'Unknown open receipt state; manual reconciliation is required.' }
    if ($DownloadOnly) { $receipt | ConvertTo-Json -Depth 8; exit 0 }
    $configPath = Join-Path $root 'receiver-config.json'
    $studio = Get-InstalledStudio $BambuStudioPath $configPath
    Write-Receipt $configPath ([ordered]@{ bambu_studio_path = $studio })
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $studio
    $start.UseShellExecute = $false
    $start.WorkingDirectory = [IO.Path]::GetDirectoryName($studio)
    if ($start.PSObject.Properties.Name -contains 'ArgumentList') { [void]$start.ArgumentList.Add($destination) }
    else { $start.Arguments = ConvertTo-WindowsArgument $destination }
    $receipt.open_status = 'launch_unknown'
    $receipt.updated_at = [DateTime]::UtcNow.ToString('o')
    Write-Receipt $receiptPath $receipt
    $process = [Diagnostics.Process]::Start($start)
    if ($null -eq $process) { throw 'Application launch outcome is unknown. The receiver will not retry automatically.' }
    $receipt.open_status = 'process_started'
    $receipt['process_id'] = $process.Id
    $receipt.updated_at = [DateTime]::UtcNow.ToString('o')
    Write-Receipt $receiptPath $receipt
    $process.Dispose()
    $receipt | ConvertTo-Json -Depth 8
} catch {
    # Avoid echoing bearer handoff URLs or arbitrary remote response bodies.
    [Console]::Error.WriteLine('justbuild receiver: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($response) { $response.Dispose() }
    if ($temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    if ($lock) { $lock.Dispose() }
    if ($client) { $client.Dispose() }
    if ($handler) { $handler.Dispose() }
}
