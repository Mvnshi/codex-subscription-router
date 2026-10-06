<#
Codex Router - what to paste into an issue.

Prints a short, read-only report: the Windows and tool versions, the Codex app this PC has
(and whether the project has recorded that build), whether Codex Router is installed and
running, and what its accounts look like. Emails, tokens and chat contents are never
printed. Nothing is changed.

  irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/scripts/doctor.ps1 | iex

or, from a clone:  powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1
#>
& {
    $ErrorActionPreference = 'Continue'

    function Show([string]$Label, $Value) { '{0,-26} {1}' -f ($Label + ':'), $Value }
    function Version-Of([string]$Command, [string[]]$Arguments) {
        $found = Get-Command $Command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $found) { return 'not found' }
        try { return ((& $found.Source @Arguments 2>&1 | Select-Object -First 1) -as [string]).Trim() } catch { return 'found, but did not run' }
    }

    'Codex Router doctor'
    '==================='
    Show 'Date' (Get-Date -Format 'yyyy-MM-dd HH:mm zzz')
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    Show 'Windows' ("{0} ({1}), {2}" -f $os.Caption, $os.Version, $env:PROCESSOR_ARCHITECTURE)
    Show 'PowerShell' $PSVersionTable.PSVersion
    ''
    'Tools'
    Show '  go' (Version-Of 'go' @('version'))
    Show '  node' (Version-Of 'node' @('--version'))
    Show '  python' (Version-Of 'python' @('--version'))
    Show '  git (optional)' (Version-Of 'git' @('--version'))
    Show '  winget' (Version-Of 'winget' @('--version'))

    ''
    'Official Codex app (Microsoft Store)'
    $package = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue | Sort-Object Version -Descending | Select-Object -First 1
    $asarHash = $null
    if ($package) {
        Show '  package' ("{0} {1} ({2})" -f $package.Name, $package.Version, $package.Architecture)
        $asar = Join-Path $package.InstallLocation 'app\resources\app.asar'
        if (Test-Path -LiteralPath $asar) {
            $asarHash = (Get-FileHash -LiteralPath $asar -Algorithm SHA256).Hash.ToLowerInvariant()
            Show '  app.asar sha256' $asarHash
        }
    } else {
        Show '  package' 'OpenAI.Codex is not installed from the Microsoft Store'
    }

    ''
    'Codex Router'
    $install = Join-Path $env:LOCALAPPDATA 'Programs\Codex Subscription Router'
    $launcher = Join-Path $install 'Codex Subscription Router.exe'
    if (Test-Path -LiteralPath $launcher) {
        Show '  installed' ("yes, built {0}" -f (Get-Item -LiteralPath $launcher).LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
        $host_ = Get-ChildItem -LiteralPath $install -Filter '*.exe' -File | Where-Object { $_.Name -notin 'Codex Subscription Router.exe' } | Select-Object -First 1
        $start = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
        Show '  start menu shortcut' $(if (Test-Path -LiteralPath (Join-Path $start 'Codex Router.lnk')) { 'Codex Router' } elseif (Test-Path -LiteralPath (Join-Path $start 'Codex Subscription Router.lnk')) { 'old name (rebuild to rename it)' } else { 'missing' })
    } else {
        Show '  installed' 'no'
    }
    $prefix = $install.TrimEnd('\') + '\'
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } catch { $false } })
    Show '  running processes' $running.Count

    $root = Join-Path $env:USERPROFILE '.codex-mux'
    $tokenFile = Join-Path $root 'control-token'
    if ((Test-Path -LiteralPath $tokenFile) -and $running.Count -gt 0) {
        try {
            $token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
            $headers = @{ 'X-Codex-Mux-Token' = $token }
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:48123/v1/health' -Headers $headers -TimeoutSec 4
            Show '  control API' $(if ($health.ok) { 'healthy' } else { 'answered, not healthy' })
            $accounts = (Invoke-RestMethod -Uri 'http://127.0.0.1:48123/v1/accounts' -Headers $headers -TimeoutSec 8)
            Show '  routing' ("{0}, resets: {1}" -f $accounts.routing.mode, $accounts.routing.resetPolicy)
            $index = 0
            foreach ($account in $accounts.accounts) {
                $index++
                $limits = $account.rateLimits
                $usage = 'usage unknown'
                if ($limits) {
                    # A single-window plan reports one window; say which windows exist.
                    $windows = @()
                    if ($limits.primary) { $windows += ("{0}-minute window {1}% used" -f $limits.primary.windowDurationMins, $limits.primary.usedPercent) }
                    if ($limits.secondary) { $windows += ("{0}-minute window {1}% used" -f $limits.secondary.windowDurationMins, $limits.secondary.usedPercent) }
                    if ($windows.Count -gt 0) { $usage = $windows -join ', ' }
                }
                Show "  account $index" ("{0}, connected={1}, enabled={2}, {3}" -f $account.planLabel, $account.connected, $account.enabled, $usage)
            }
        } catch {
            Show '  control API' ("not reachable ({0})" -f $_.Exception.Message)
        }
    }
    $providerFile = Join-Path $root 'engine-provider'
    Show '  engine provider' $(if ($env:CODEX_MUX_ENGINE_PROVIDER) { "env: $env:CODEX_MUX_ENGINE_PROVIDER" } elseif (Test-Path -LiteralPath $providerFile) { "file: " + (Get-Content -LiteralPath $providerFile -TotalCount 1) } else { 'default' })
    $backups = Join-Path $root 'backups'
    if (Test-Path -LiteralPath $backups) {
        $count = @(Get-ChildItem -LiteralPath $backups -Directory).Count
        Show '  rebuild backups' ("{0} (about 2 GB each, in {1})" -f $count, $backups)
    }

    ''
    'Is this Codex build recorded by the project?'
    $patcher = $null
    if ($PSCommandPath) { $patcher = Join-Path (Split-Path -Parent (Split-Path -Parent $PSCommandPath)) 'scripts\patch_app_windows.py' }
    $sourceDir = Join-Path $env:USERPROFILE '.codex-subscription-router-mvnshi\source'
    foreach ($candidate in @($patcher, (Join-Path $sourceDir 'scripts\patch_app_windows.py'))) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $patcher = $candidate; break }
    }
    $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($patcher -and (Test-Path -LiteralPath $patcher) -and $python -and $package) {
        $output = & $python.Source $patcher '--check-source' 2>&1 | ForEach-Object { "  $_" }
        $output
        'exit code: ' + $LASTEXITCODE + '  (0 = recorded or allowed, 3 = newer than the recorded builds, 1 = something else)'
    } else {
        '  skipped (needs Python, the project source and the Store app)'
    }
}
