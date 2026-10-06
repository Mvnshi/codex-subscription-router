<#
Codex Router - uninstaller (Windows).

Removes the router's copy of the app, its shortcuts and the router window's own profile, and says
what it removed. The official Codex app is never touched, and neither is your normal Codex data in
%USERPROFILE%\.codex.

  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1             remove the app and shortcuts
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -WhatIf     only show what would be removed
  ... -RemoveAccounts   also delete the router's own state in %USERPROFILE%\.codex-mux: the sign-ins of
                        its additional subscriptions and which account owns which chat (backups stay)
  ... -RemoveBackups    also delete the rebuild backups in %USERPROFILE%\.codex-mux\backups (about 2 GB each)

The downloaded source (%USERPROFILE%\.codex-subscription-router-mvnshi) is left in place; delete that
folder yourself if you also want it gone.
#>
#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$RemoveAccounts,
    [switch]$RemoveBackups
)

& {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'

    $installDir = Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Programs\Codex Subscription Router'
    $launcher = Join-Path -Path $installDir -ChildPath 'Codex Subscription Router.exe'
    $profileDir = Join-Path -Path $env:APPDATA -ChildPath 'Codex Subscription Router'
    $stateRoot = Join-Path -Path $env:USERPROFILE -ChildPath '.codex-mux'
    $startMenu = Join-Path -Path $env:APPDATA -ChildPath 'Microsoft\Windows\Start Menu\Programs'
    $desktop = [Environment]::GetFolderPath('Desktop')
    $removed = [System.Collections.Generic.List[string]]::new()

    function Remove-Path {
        param([string]$Path, [string]$What)
        if (-not (Test-Path -LiteralPath $Path)) { return }
        if ($PSCmdlet.ShouldProcess($Path, "Remove $What")) {
            Remove-Item -LiteralPath $Path -Recurse -Force
            $removed.Add("$What ($Path)")
        } else {
            $removed.Add("would remove $What ($Path)")
        }
    }

    # 1. Quit the router: every process that runs from the install folder.
    $prefix = $installDir.TrimEnd('\') + '\'
    foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
        $path = $null
        try { $path = $process.Path } catch { $path = $null }
        if ($path -and $path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            if ($PSCmdlet.ShouldProcess("$($process.ProcessName) ($($process.Id))", 'Stop')) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if (-not $WhatIfPreference) { Start-Sleep -Seconds 2 }

    # 2. Shortcuts, but only ones that open this copy (the old name is cleaned up too).
    $shell = New-Object -ComObject WScript.Shell
    foreach ($directory in @($startMenu, $desktop)) {
        if ([string]::IsNullOrWhiteSpace($directory)) { continue }
        foreach ($name in @('Codex Router.lnk', 'Codex Subscription Router.lnk')) {
            $shortcut = Join-Path -Path $directory -ChildPath $name
            if (-not (Test-Path -LiteralPath $shortcut)) { continue }
            $target = $shell.CreateShortcut($shortcut).TargetPath
            if ($target -ieq $launcher) {
                Remove-Path -Path $shortcut -What 'shortcut'
            }
        }
    }

    # 3. The app and the router window's own profile.
    Remove-Path -Path $installDir -What 'the app'
    Remove-Path -Path $profileDir -What "the router window's profile"

    # 4. The router's state, only when asked.
    if ($RemoveAccounts -and (Test-Path -LiteralPath $stateRoot)) {
        foreach ($entry in Get-ChildItem -LiteralPath $stateRoot -Force) {
            if ($entry.Name -eq 'backups' -and -not $RemoveBackups) { continue }
            Remove-Path -Path $entry.FullName -What 'router state'
        }
    }
    if ($RemoveBackups) {
        Remove-Path -Path (Join-Path -Path $stateRoot -ChildPath 'backups') -What 'rebuild backups'
    }

    ''
    if ($removed.Count -eq 0) {
        'Nothing to remove: Codex Router is not installed here.'
    } else {
        $removed | ForEach-Object { "  $_" }
    }
    if (-not $RemoveAccounts -and (Test-Path -LiteralPath $stateRoot)) {
        "Kept: $stateRoot (the router's accounts and chat ownership; add -RemoveAccounts to delete it)."
    }
    'Your normal Codex app and %USERPROFILE%\.codex were not touched.'
}
