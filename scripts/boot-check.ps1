<#
Starts the installed Codex Router and checks that it really boots: the multiplexer's control API
answers, and the app's own window loaded its page. Used by the upstream canary on a clean machine;
it also works locally when the router is not already running (a second launch only focuses the
running one).

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\boot-check.ps1 [-TimeoutSeconds 120]

Prints "boot: ok (...)" or "boot: failed (...)" and exits 0 or 1. The router is stopped afterwards.
It runs with the app's UI-test bridge on (CODEX_MUX_UI_TESTS=1) because the bridge is what reports
the page state; the bridge only listens on 127.0.0.1 and needs the router's control token.
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\Codex Subscription Router'),
    [int]$TimeoutSeconds = 120
)
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $InstallDir 'Codex Subscription Router.exe'
$prefix = $InstallDir.TrimEnd('\') + '\'

function Stop-Router {
    foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
        $path = $null
        try { $path = $process.Path } catch { $path = $null }
        if ($path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host "boot: failed (no launcher at $launcher)"
    exit 1
}

try {
    $env:CODEX_MUX_UI_TESTS = '1'
    Start-Process -FilePath $launcher
    $env:CODEX_MUX_UI_TESTS = $null
    $tokenFile = Join-Path $env:USERPROFILE '.codex-mux\control-token'
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    # 1. The multiplexer's control API.
    $healthy = $false
    $headers = $null
    while ((Get-Date) -lt $deadline -and -not $healthy) {
        if (Test-Path -LiteralPath $tokenFile) {
            $headers = @{ 'X-Codex-Mux-Token' = (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
            try {
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:48123/v1/health' -Headers $headers -TimeoutSec 3
                $healthy = [bool]$health.ok
            } catch { }
        }
        if (-not $healthy) { Start-Sleep -Seconds 2 }
    }
    if (-not $healthy) {
        Write-Host "boot: failed (the control API did not answer within $TimeoutSeconds seconds)"
        exit 1
    }

    # 2. The app's window loaded its page and drew something: a document that is complete but has
    # no text is a blank or crashed renderer, which is not a boot.
    $state = $null
    $blank = $false
    while ((Get-Date) -lt $deadline -and $null -eq $state) {
        try {
            $candidate = Invoke-RestMethod -Uri 'http://127.0.0.1:48124/v1/test/app-state?debug=1' -Headers $headers -TimeoutSec 60
            if ($candidate.debug.readyState -eq 'complete' -and "$($candidate.debug.href)".StartsWith('app://')) {
                if ("$($candidate.debug.bodyText)".Trim().Length -gt 0) { $state = $candidate } else { $blank = $true }
            }
        } catch { }
        if ($null -eq $state) { Start-Sleep -Seconds 3 }
    }
    if ($null -eq $state) {
        if ($blank) { Write-Host 'boot: failed (the control API answered and the window loaded its document, but it drew nothing)' }
        else { Write-Host "boot: failed (the control API answered but the app's window never finished loading)" }
        exit 1
    }
    $text = (("$($state.debug.bodyText)" -replace '\s+', ' ').Trim())
    if ($text.Length -gt 60) { $text = $text.Substring(0, 60) }
    Write-Host "boot: ok (control API healthy; window $($state.bounds.width)x$($state.bounds.height) loaded: `"$text`")"
    exit 0
} catch {
    Write-Host "boot: failed ($($_.Exception.Message))"
    exit 1
} finally {
    $env:CODEX_MUX_UI_TESTS = $null
    Stop-Router
}
