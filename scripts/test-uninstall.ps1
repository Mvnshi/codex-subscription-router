<#
Runs scripts\uninstall.ps1 against a sandbox: LOCALAPPDATA, APPDATA and USERPROFILE point into a
temporary folder, so nothing real is touched. Checks that -WhatIf removes nothing, that the default run
removes the app, its profile and only the shortcuts that open it, that the router's state and backups
stay unless asked for, and that the normal Codex data is never touched.

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-uninstall.ps1

Exits 1 if anything is wrong. Windows only (it creates real .lnk files).
#>
$ErrorActionPreference = 'Stop'
$uninstaller = (Resolve-Path -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath 'uninstall.ps1')).Path
$sandbox = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('csr-uninstall-' + [guid]::NewGuid().ToString('N'))
$failures = 0

function Check {
    param([string]$Name, [bool]$Condition)
    if ($Condition) { Write-Host "ok    $Name" } else { $script:failures++; Write-Host "FAIL  $Name" }
}

try {
    $local = Join-Path $sandbox 'Local'
    $roaming = Join-Path $sandbox 'Roaming'
    $user = Join-Path $sandbox 'User'
    $install = Join-Path $local 'Programs\Codex Subscription Router'
    $profileDir = Join-Path $roaming 'Codex Subscription Router'
    $startMenu = Join-Path $roaming 'Microsoft\Windows\Start Menu\Programs'
    $state = Join-Path $user '.codex-mux'
    $normal = Join-Path $user '.codex'
    foreach ($directory in $install, $profileDir, $startMenu, (Join-Path $state 'accounts\a'), (Join-Path $state 'backups\20260101-000000'), $normal) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    Set-Content -LiteralPath (Join-Path $install 'Codex Subscription Router.exe') -Value 'fake'
    Set-Content -LiteralPath (Join-Path $profileDir 'Local State') -Value 'x'
    Set-Content -LiteralPath (Join-Path $state 'state.json') -Value '{}'
    Set-Content -LiteralPath (Join-Path $state 'accounts\a\auth.json') -Value 'secret'
    Set-Content -LiteralPath (Join-Path $state 'backups\20260101-000000\marker') -Value 'old copy'
    Set-Content -LiteralPath (Join-Path $normal 'auth.json') -Value 'normal codex data'
    Set-Content -LiteralPath (Join-Path $sandbox 'other.exe') -Value 'someone else'

    $shell = New-Object -ComObject WScript.Shell
    function New-Shortcut([string]$Path, [string]$Target) { $s = $shell.CreateShortcut($Path); $s.TargetPath = $Target; $s.Save() }
    $ours = Join-Path $startMenu 'Codex Router.lnk'
    $oursOldName = Join-Path $startMenu 'Codex Subscription Router.lnk'
    New-Shortcut $ours (Join-Path $install 'Codex Subscription Router.exe')
    # A shortcut with the old name that opens something else must be left alone.
    New-Shortcut $oursOldName (Join-Path $sandbox 'other.exe')

    function Run-Uninstaller([string[]]$Arguments) {
        $command = "`$env:LOCALAPPDATA='$local'; `$env:APPDATA='$roaming'; `$env:USERPROFILE='$user'; & '$uninstaller' @args"
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -Command $command @Arguments 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 exited $LASTEXITCODE`n$output" }
        return $output
    }

    # -WhatIf changes nothing.
    $preview = Run-Uninstaller @('-WhatIf')
    Check '-WhatIf removes nothing' ((Test-Path $install) -and (Test-Path $profileDir) -and (Test-Path $ours))
    Check '-WhatIf says what it would remove' ($preview -match 'would remove')

    # The default run.
    $output = Run-Uninstaller @()
    Check 'the app is removed' (-not (Test-Path $install))
    Check "the router window's profile is removed" (-not (Test-Path $profileDir))
    Check 'the shortcut that opens the app is removed' (-not (Test-Path $ours))
    Check 'a same-named shortcut that opens something else is kept' (Test-Path $oursOldName)
    Check "the router's accounts and backups are kept" ((Test-Path (Join-Path $state 'accounts\a\auth.json')) -and (Test-Path (Join-Path $state 'backups')))
    Check 'normal Codex data is untouched' ((Get-Content (Join-Path $normal 'auth.json')) -eq 'normal codex data')
    Check 'the report says the accounts were kept' ($output -match 'Kept:')

    # Nothing left to remove is not an error.
    $again = Run-Uninstaller @()
    Check 'running again reports there is nothing to remove' ($again -match 'Nothing to remove')

    # -RemoveAccounts clears the state but keeps backups; -RemoveBackups then clears those.
    Run-Uninstaller @('-RemoveAccounts') | Out-Null
    Check '-RemoveAccounts removes accounts and state' (-not (Test-Path (Join-Path $state 'accounts')) -and -not (Test-Path (Join-Path $state 'state.json')))
    Check '-RemoveAccounts keeps the backups' (Test-Path (Join-Path $state 'backups\20260101-000000\marker'))
    Run-Uninstaller @('-RemoveBackups') | Out-Null
    Check '-RemoveBackups removes the backups' (-not (Test-Path (Join-Path $state 'backups')))
    Check 'normal Codex data is still untouched' (Test-Path (Join-Path $normal 'auth.json'))
} finally {
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($failures -ne 0) { Write-Host "$failures uninstall check(s) failed"; exit 1 }
Write-Host 'all uninstall checks passed'
