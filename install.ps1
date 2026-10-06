<#
Codex Subscription Router - Windows installer.

This is the Windows counterpart of install.sh: it downloads or updates the
source, installs the locked build tools, builds the independent copy of the
official desktop app with scripts\patch_app_windows.py, and launches it. The
official installation is only read; it is never modified.

Nothing needs to be installed first. When Go, Node.js or Python is missing the
installer offers to install it with winget (Windows asks for permission for each
one), and it does not need Git: without Git it downloads the source as a zip.
When the Store app is a newer build than the project has recorded, it explains
that in plain words and asks before going on.

Two ways to run it:

  1. The documented one-liner. Invoke-Expression cannot pass parameters, so
     every option is also read from an environment variable (set them in the
     same PowerShell session before running the line):

       $env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR = 'D:\src\codex-subscription-router'  # optional
       $env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE = '1'                       # optional
       $env:CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH = '1'                                   # optional
       $env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES = '1'                                  # optional
       $env:CODEX_SUBSCRIPTION_ROUTER_NO_DESKTOP_SHORTCUT = '1'                         # optional
       irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.ps1 | iex

  2. From a clone or a downloaded copy, with ordinary parameters:

       powershell -ExecutionPolicy Bypass -File .\install.ps1 [-SourceDir <path>] [-AllowUntestedSource]
                  [-NoLaunch] [-Yes] [-NoDesktopShortcut]

  -Yes answers the installer's questions (install missing tools, continue with a
  newer app build) for you; without it, a session nobody can answer in stops with
  the exact command or setting that would have answered the question.

  Environment variables are honoured in both styles. -SourceDir wins over the
  variable when both are given; the switches are enabled by either form. From a
  clone (a directory that also holds scripts\patch_app_windows.py) the clone
  itself is always built and -SourceDir / CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR
  are ignored, exactly as install.sh ignores them; they only choose where the
  one-liner, or a copy of this file outside the repository, keeps its checkout.

  CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1 makes dot-sourcing this file define the
  functions without running the installation, so the repository checks can
  exercise them without installing anything.
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$SourceDir,
    [switch]$AllowUntestedSource,
    [switch]$NoLaunch,
    [switch]$Yes,
    [switch]$NoDesktopShortcut
)

# Everything below, except the param() block above, lives in this script block. Under
# `irm ... | iex` Invoke-Expression runs the text in the user's interactive scope, where strict
# mode, the preference variables, and every function and constant defined here would otherwise
# survive the installation (Write-Log or Fail would even replace functions of the same name from
# the user's profile; strict mode cannot be saved and restored because there is no Get-StrictMode).
# The block is invoked with `&` at the end of the file, which gives its contents a scope that ends
# with the run in both invocation styles; the param() variables remain readable inside it, and
# Fail's `exit`/`throw` work as before. The param() block itself has to stay outside, and no
# scoping trick covers it: under `irm ... | iex` the parameter binder assigns $SourceDir,
# $AllowUntestedSource, $NoLaunch, $Yes and $NoDesktopShortcut in the caller's scope, so caller
# variables of those five names that already exist are overwritten with the bound defaults
# ('' / False / False / False / False) and stay that way after the run (when none exist
# beforehand, nothing is left behind).
# The body is deliberately not indented: it is the whole installer.
$installer = {
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# PowerShell never throws on a non-zero native exit code, so every native command below checks
# $LASTEXITCODE itself. PowerShell 7.4+ can optionally turn such exit codes into terminating
# errors before this script sees them; keep that off so failures are reported through Fail with
# the same message in every PowerShell version (Windows PowerShell 5.1 ignores this variable).
$PSNativeCommandUseErrorActionPreference = $false

# ---------------------------------------------------------------------------------------------
# Constants (mirroring install.sh).
# ---------------------------------------------------------------------------------------------
$RepositoryUrl = 'https://github.com/Mvnshi/codex-subscription-router.git'
$SourceBranch = 'main'
# USERPROFILE only places the default source checkout, which this script manages itself, so
# [Environment]::GetFolderPath (the same source Windows fills the variable from) may stand in for a
# session that lacks it.
$UserProfileDir = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
$DefaultSourceDir = Join-Path -Path $UserProfileDir -ChildPath '.codex-subscription-router-mvnshi\source'
# LOCALAPPDATA gets no such fallback: scripts\patch_app_windows.py derives the destination from
# that variable alone and fails closed without it, and this script never passes --destination, so
# a substitute here would announce, stop, and check a location the patcher does not use.
# Assert-Prerequisite fails when it is missing (Fail is not defined yet at this point, and
# Join-Path rejects an empty -Path); until then the destination paths stay empty.
$LocalAppDataDir = [string]$env:LOCALAPPDATA
$DestinationDir = ''
$Launcher = ''
if (-not [string]::IsNullOrWhiteSpace($LocalAppDataDir)) {
    $DestinationDir = Join-Path -Path $LocalAppDataDir -ChildPath 'Programs\Codex Subscription Router'
    $Launcher = Join-Path -Path $DestinationDir -ChildPath 'Codex Subscription Router.exe'
}
$PatcherRelativePath = 'scripts\patch_app_windows.py'
# The same source as `git clone --branch main`, for a PC without Git.
$ArchiveUrl = 'https://github.com/Mvnshi/codex-subscription-router/archive/refs/heads/main.zip'
# Marks a directory this installer filled from the archive, so it is only ever replaced
# when it is one of ours.
$SnapshotMarker = '.codex-router-source-snapshot'
# Exit code of `patch_app_windows.py --check-source` when the app is a build the project has
# not recorded (the patcher's UntestedSourceError).
$UntestedSourceExitCode = 3
$WingetPackages = [ordered]@{
    go = 'GoLang.Go'
    node = 'OpenJS.NodeJS.LTS'
    npm = 'OpenJS.NodeJS.LTS'
    python = 'Python.Python.3.12'
}
$FriendlyToolNames = @{
    go = 'Go'
    node = 'Node.js'
    npm = 'Node.js'
    python = 'Python'
}
$MinimumNodeVersion = [version]'22.12.0'
$MinimumGoVersion = [version]'1.26.0'
$MinimumPythonVersion = [version]'3.11.0'

# Options are merged once here from the parameters and the environment so every function reads
# one object regardless of how the script was invoked.
$Options = [pscustomobject]@{
    SourceDir = if (-not [string]::IsNullOrWhiteSpace($SourceDir)) {
        $SourceDir
    } elseif (-not [string]::IsNullOrWhiteSpace($env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR)) {
        $env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR
    } else {
        $DefaultSourceDir
    }
    # Whether a directory was asked for at all, so Resolve-SourceDir can say when a clone overrides it.
    SourceDirRequested = ((-not [string]::IsNullOrWhiteSpace($SourceDir)) -or (-not [string]::IsNullOrWhiteSpace($env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR)))
    AllowUntestedSource = ($AllowUntestedSource.IsPresent -or ($env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE -eq '1'))
    NoLaunch = ($NoLaunch.IsPresent -or ($env:CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH -eq '1'))
    AssumeYes = ($Yes.IsPresent -or ($env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES -eq '1'))
    NoDesktopShortcut = ($NoDesktopShortcut.IsPresent -or ($env:CODEX_SUBSCRIPTION_ROUTER_NO_DESKTOP_SHORTCUT -eq '1'))
}

# Path of this file when it runs from disk (a clone, a download, or dot-sourcing); empty under
# `irm ... | iex`. Get-Variable keeps Set-StrictMode quiet on hosts where the automatic variable
# is absent.
$InstallerPath = ''
$commandPathVariable = Get-Variable -Name PSCommandPath -ErrorAction SilentlyContinue
if ($null -ne $commandPathVariable -and -not [string]::IsNullOrWhiteSpace([string]$commandPathVariable.Value)) {
    $InstallerPath = [string]$commandPathVariable.Value
}

# ---------------------------------------------------------------------------------------------
# Output helpers (same wording as install.sh).
# ---------------------------------------------------------------------------------------------
function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ''
    Write-Host "==> $Message"
}

function Fail {
    param([Parameter(Mandatory = $true)][string]$Reason)
    Write-Host ''
    Write-Host "Install failed: $Reason"
    if ($InstallerPath) {
        # Running from a file: exit 1 like install.sh does.
        exit 1
    }
    # Running via `irm ... | iex`: `exit` would close the user's console window together with the
    # message above, so raise a terminating error instead. PowerShell prints it, stops the
    # installation, and the session stays open. The reason is already on the console, so the
    # error carries a short marker rather than repeating it.
    throw 'Installation stopped.'
}

# ---------------------------------------------------------------------------------------------
# Native command helpers.
# ---------------------------------------------------------------------------------------------
function Format-CommandLine {
    param([string[]]$Tokens)
    $quotedTokens = foreach ($token in $Tokens) {
        if ($token -match '[\s"]') { '"' + ($token -replace '"', '\"') + '"' } else { $token }
    }
    return ($quotedTokens -join ' ')
}

function Invoke-NativeCommand {
    <#
    Runs a native command whose output belongs on the console (npm, git pull, the patcher).
    Output is sent to the host explicitly: an uncaptured native command inside a function would
    otherwise become that function's return value and corrupt callers that return a path.
    Standard error and the exit code are treated exactly as in Get-NativeOutput (see there).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    & $Command @Arguments | Out-Host
    if ($null -eq $LASTEXITCODE) {
        Fail "$Description could not be started ('$Command' did not run)."
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "$Description failed with exit code $LASTEXITCODE."
    }
}

function Invoke-NativeCommandForExitCode {
    <#
    Like Invoke-NativeCommand, but returns the exit code instead of failing on a non-zero one,
    for commands whose particular exit codes mean something to the caller (the patcher's
    --check-source). A command that cannot be started at all still fails.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    & $Command @Arguments | Out-Host
    if ($null -eq $LASTEXITCODE) {
        Fail "$Description could not be started ('$Command' did not run)."
    }
    return [int]$LASTEXITCODE
}

function Get-NativeOutput {
    <#
    Runs a native command and returns its standard output (trimmed) with the exit code.

    Standard error is not redirected, and $ErrorActionPreference is lowered to Continue for the
    call (the assignment is local to this function and ends with it). Windows PowerShell 5.1
    wraps native stderr lines into NativeCommandError records not only under an in-script 2> or
    2>&1 but also whenever powershell.exe has no console of its own (the ISE and other GUI hosts,
    a parent that captures both streams, CREATE_NO_WINDOW/DETACHED_PROCESS spawns), and 'Stop'
    would turn the first such line -- git's "Cloning into ...", an npm warning -- into a
    terminating error before the exit code is read. PowerShell 7 never applies the preference to
    native stderr. The exit code stays the only verdict: under Continue a command that cannot
    start at all (removed since the prerequisite check, blocked by policy) reports a
    non-terminating error and leaves $LASTEXITCODE untouched, so it is cleared first and a
    missing value fails closed instead of inheriting the previous command's 0.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    $lines = @(& $Command @Arguments)
    if ($null -eq $LASTEXITCODE) {
        Fail "'$Command' could not be started."
    }
    $exitCode = $LASTEXITCODE
    $text = (($lines | ForEach-Object { [string]$_ }) -join "`n").Trim()
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $text }
}

function ConvertTo-Version {
    # Accepts "v22.12.0", "go1.26.1", "go1.26rc1", "3.11.4" and returns a [version] or $null.
    param([string]$Text)
    $match = [regex]::Match([string]$Text, '(\d+)\.(\d+)(?:\.(\d+))?')
    if (-not $match.Success) {
        return $null
    }
    $build = if ($match.Groups[3].Success) { [int]$match.Groups[3].Value } else { 0 }
    return [version]::new([int]$match.Groups[1].Value, [int]$match.Groups[2].Value, $build)
}

function Test-ApplicationCommand {
    param([Parameter(Mandatory = $true)][string]$Name)
    return ($null -ne (Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue))
}

function Get-ApplicationPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return ''
    }
    return [string]$command.Source
}

# ---------------------------------------------------------------------------------------------
# Prerequisites.
# ---------------------------------------------------------------------------------------------
function Test-WindowsHost {
    if ($env:OS -ne 'Windows_NT') {
        return $false
    }
    # PowerShell 7 defines $IsLinux/$IsMacOS (an emulated Windows_NT environment could still set
    # OS); Windows PowerShell 5.1 does not define them and Set-StrictMode would turn a bare
    # reference into an error, so look them up with Get-Variable.
    foreach ($variableName in @('IsLinux', 'IsMacOS')) {
        $variable = Get-Variable -Name $variableName -ErrorAction SilentlyContinue
        if ($null -ne $variable -and $variable.Value -eq $true) {
            return $false
        }
    }
    return $true
}

function Test-Elevated {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object -TypeName System.Security.Principal.WindowsPrincipal -ArgumentList $identity
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } finally {
        $identity.Dispose()
    }
}

function Resolve-PythonInterpreter {
    <#
    Chooses "python" or "py -3", whichever runs and is Python 3.11+. "python" is tried first so an
    activated virtual environment or an explicit PATH entry wins; the Microsoft Store placeholder
    python.exe only prints an advertisement and exits non-zero, which the probe treats like any
    other unusable candidate before falling back to the "py" launcher.
    #>
    $candidates = @(
        [pscustomobject]@{ Command = 'python'; Arguments = [string[]]@() },
        [pscustomobject]@{ Command = 'py'; Arguments = [string[]]@('-3') }
    )
    $available = @($candidates | Where-Object { Test-ApplicationCommand -Name $_.Command })
    if ($available.Count -eq 0) {
        return $null
    }

    # No quotation marks inside the probe: Windows PowerShell 5.1 does not escape embedded double
    # quotes when it quotes an argument that contains spaces.
    $probeCode = 'import sys; print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro)'
    $reports = @()
    foreach ($candidate in $available) {
        $display = Format-CommandLine -Tokens (@($candidate.Command) + @($candidate.Arguments))
        $probe = Get-NativeOutput -Command $candidate.Command -Arguments (@($candidate.Arguments) + @('-c', $probeCode))
        if ($probe.ExitCode -ne 0) {
            $reports += "'$display' exited with code $($probe.ExitCode)"
            continue
        }
        $version = ConvertTo-Version -Text ($probe.Output -replace '\s+', '.')
        if ($null -eq $version) {
            $reports += "'$display' printed '$($probe.Output)' instead of a version"
            continue
        }
        if ($version -lt $MinimumPythonVersion) {
            $reports += "'$display' is Python $version"
            continue
        }
        return [pscustomobject]@{
            Command = $candidate.Command
            Arguments = [string[]]$candidate.Arguments
            Display = $display
            Version = $version
            Path = Get-ApplicationPath -Name $candidate.Command
            Reports = [string[]]$reports
        }
    }
    return [pscustomobject]@{
        Command = ''
        Arguments = [string[]]@()
        Display = ''
        Version = $null
        Path = ''
        Reports = [string[]]$reports
    }
}

function Test-Interactive {
    # True when a person can answer a question here: a real console whose input is not redirected
    # (under `irm ... | iex` in a normal window it is; a script run by another program is not).
    if (-not [Environment]::UserInteractive) {
        return $false
    }
    try {
        return (-not [Console]::IsInputRedirected)
    } catch {
        return $false
    }
}

function Update-SessionPath {
    # A tool installed a moment ago (by winget, say) is on the machine and user PATH but not on the
    # PATH this session started with, which is why the installer used to say "open a new window".
    # Merge the registry's current values in front of the session's, without repeating entries.
    $merged = [System.Collections.Generic.List[string]]::new()
    $values = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    )
    foreach ($value in $values) {
        foreach ($entry in ([string]$value -split ';')) {
            $trimmed = $entry.Trim()
            if ($trimmed -and -not ($merged | Where-Object { $_ -ieq $trimmed })) {
                $merged.Add($trimmed)
            }
        }
    }
    $env:Path = $merged -join ';'
}

function Get-PrerequisiteStatus {
    # Which build tools are absent. Git is not one of them: the source can come as a zip.
    $missing = @()
    foreach ($commandName in @('go', 'node', 'npm')) {
        if (-not (Test-ApplicationCommand -Name $commandName)) {
            $missing += $commandName
        }
    }
    $python = Resolve-PythonInterpreter
    if ($null -eq $python -or [string]::IsNullOrWhiteSpace($python.Command)) {
        $missing += 'python'
    }
    return [pscustomobject]@{ Missing = [string[]]$missing; Python = $python }
}

function Install-MissingPrerequisite {
    <#
    Offers to install what is missing with winget, which ships with current Windows 10 and 11.
    Windows asks the user to allow each installer (that prompt is Windows', not ours). Fails with
    the exact commands when it cannot or may not do it.
    #>
    param([Parameter(Mandatory = $true)][string[]]$Missing)
    $packages = @($Missing | ForEach-Object { $WingetPackages[$_] } | Select-Object -Unique)
    $names = @($Missing | ForEach-Object { $FriendlyToolNames[$_] } | Select-Object -Unique)
    $manualCommands = ($packages | ForEach-Object { "  winget install --id $_ -e" }) -join "`n"

    Write-Host ''
    Write-Host "This PC needs $($names -join ', ') to build Codex Router. They are free, open-source tools."
    if (-not (Test-ApplicationCommand -Name 'winget')) {
        Fail ("missing prerequisites: $($Missing -join ' '). winget (the Windows package manager, part of 'App Installer' in the Microsoft Store) was not found, so they cannot be installed automatically. " +
            "Install Go 1.26+, Node.js 22.12+ (with npm) and Python 3.11+, then run this command again.")
    }
    if (-not $Options.AssumeYes) {
        if (-not (Test-Interactive)) {
            Fail ("missing prerequisites: $($Missing -join ' '). Nobody can be asked here, so nothing was installed. Either set `$env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES = '1' (or pass -Yes) to let the installer install them with winget, or install them yourself and run this command again:`n$manualCommands")
        }
        $answer = Read-Host 'Install them now with winget? [Y/n]'
        if ($answer -match '^\s*(n|no)\s*$') {
            Fail "missing prerequisites: $($Missing -join ' '). Install them, then run this command again:`n$manualCommands"
        }
    }
    foreach ($package in $packages) {
        Write-Log "Installing $package"
        Invoke-NativeCommand -Description "winget install $package" -Command 'winget' -Arguments @(
            'install', '--id', $package, '-e', '--silent', '--accept-package-agreements', '--accept-source-agreements'
        )
    }
    Update-SessionPath
}

function Assert-Prerequisite {
    <#
    Fails closed on anything the build needs, installing missing tools first when allowed. Returns
    the resolved toolchain (which Python invocation to use) so Main does not probe twice. The
    official desktop installation itself is not checked here: its Windows layout is discovered and
    verified by scripts\patch_app_windows.py with exact checks, and duplicating a guess here would
    only weaken that.
    #>
    if (-not (Test-WindowsHost)) {
        Fail 'Codex Subscription Router supports Windows only.'
    }

    # The same verdict scripts\patch_app_windows.py gives (see the constants above), reached before
    # anything names the destination; the elevation message below is the first to.
    if ([string]::IsNullOrWhiteSpace($LocalAppDataDir)) {
        Fail 'LOCALAPPDATA is not set.'
    }

    $architecture = [string]$env:PROCESSOR_ARCHITECTURE
    if ($architecture -notin @('AMD64', 'ARM64')) {
        $hint = ''
        if (-not [string]::IsNullOrWhiteSpace($env:PROCESSOR_ARCHITEW6432)) {
            $hint = ' This PowerShell is a 32-bit process; start the 64-bit PowerShell and rerun this command.'
        }
        Fail "Codex Subscription Router currently requires 64-bit Windows (x64 or ARM64); found '$architecture'.$hint"
    }

    if (Test-Elevated) {
        Fail "this installer is running as administrator. Codex Subscription Router installs per user under $DestinationDir; rerun it from a normal (non-elevated) PowerShell."
    }

    $status = Get-PrerequisiteStatus
    if ($status.Missing.Count -ne 0) {
        Install-MissingPrerequisite -Missing $status.Missing
        $status = Get-PrerequisiteStatus
        if ($status.Missing.Count -ne 0) {
            Fail ("installed what was missing, but $($status.Missing -join ' ') still cannot be found from this window. " +
                'Close this PowerShell window, open a new one, and run the same command again.')
        }
    }
    $python = $status.Python

    # PowerShell's command search tries <name>.ps1 before the PATHEXT extensions, and Node.js ships
    # npm.ps1 beside npm.cmd, so a bare `npm` would run the .ps1 shim: Windows PowerShell 5.1's
    # default Restricted policy refuses it (the documented one-liner would die on `npm ci` with a
    # raw "running scripts is disabled" error instead of a Fail message), and the shim rebuilds
    # its arguments through Invoke-Expression. Get-ApplicationPath returns npm.cmd, the file the
    # check above actually verified, which no execution policy gates.
    $npmPath = Get-ApplicationPath -Name 'npm'
    if ([string]::IsNullOrWhiteSpace($npmPath)) {
        Fail 'could not resolve the path of npm.'
    }

    $nodeProbe = Get-NativeOutput -Command 'node' -Arguments @('-p', 'process.versions.node')
    $nodeVersion = ConvertTo-Version -Text $nodeProbe.Output
    if ($nodeProbe.ExitCode -ne 0 -or $null -eq $nodeVersion) {
        Fail "could not determine the Node.js version ('node -p process.versions.node' exited with code $($nodeProbe.ExitCode))."
    }
    if ($nodeVersion -lt $MinimumNodeVersion) {
        Fail "Node.js 22.12 or newer is required; found v$nodeVersion. Update it with: winget upgrade --id OpenJS.NodeJS.LTS -e"
    }

    $goProbe = Get-NativeOutput -Command 'go' -Arguments @('env', 'GOVERSION')
    $goVersion = ConvertTo-Version -Text $goProbe.Output
    if ($goProbe.ExitCode -ne 0 -or $null -eq $goVersion) {
        Fail "could not determine the Go version ('go env GOVERSION' exited with code $($goProbe.ExitCode))."
    }
    if ($goVersion -lt $MinimumGoVersion) {
        Fail "Go 1.26 or newer is required; found $($goProbe.Output). Update it with: winget upgrade --id GoLang.Go -e"
    }

    if ([string]::IsNullOrWhiteSpace($python.Command)) {
        Fail "Python 3.11 or newer is required; $($python.Reports -join '; '). Install it with: winget install --id Python.Python.3.12 -e"
    }
    Write-Host "Using Python $($python.Version) via '$($python.Display)' ($($python.Path))."

    return [pscustomobject]@{
        PythonCommand = $python.Command
        PythonArguments = [string[]]$python.Arguments
        PythonDisplay = $python.Display
        NpmPath = $npmPath
        NodeVersion = $nodeVersion
        GoVersion = $goVersion
    }
}

# ---------------------------------------------------------------------------------------------
# Source checkout (same rules as install.sh).
# ---------------------------------------------------------------------------------------------
function Get-NormalizedDirectoryPath {
    <#
    Strips trailing path separators. Windows PowerShell 5.1 wraps a native argument that contains
    spaces in "..." without escaping a trailing backslash, so "D:\my projects\router\" (what tab
    completion produces) reaches git as `D:\my projects\router"` because the C runtime reads \"
    as a literal quote. A drive root keeps its backslash: a bare "D:" would mean that drive's
    current directory.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $trimmed = $Path.TrimEnd('\', '/')
    if ($trimmed -match '^[A-Za-z]:$') {
        return $trimmed + '\'
    }
    if ([string]::IsNullOrEmpty($trimmed)) {
        return $Path
    }
    return $trimmed
}

function Save-SourceSnapshot {
    <#
    Fills $Destination from the project's main-branch zip, for a PC without Git (a later run
    replaces it the same way). Only a missing directory or one carrying our marker is replaced.
    #>
    param([Parameter(Mandatory = $true)][string]$Destination)
    $temporary = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('csr-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $temporary | Out-Null
    try {
        Write-Log 'Downloading source'
        $archive = Join-Path -Path $temporary -ChildPath 'source.zip'
        $previousProgress = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $ArchiveUrl -OutFile $archive
        } catch {
            Fail "could not download $ArchiveUrl ($($_.Exception.Message))."
        } finally {
            $ProgressPreference = $previousProgress
        }
        $expanded = Join-Path -Path $temporary -ChildPath 'expanded'
        Expand-Archive -LiteralPath $archive -DestinationPath $expanded -Force
        $roots = @(Get-ChildItem -LiteralPath $expanded -Directory)
        if ($roots.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path -Path $roots[0].FullName -ChildPath $PatcherRelativePath) -PathType Leaf)) {
            Fail 'the downloaded source archive does not look like this project.'
        }
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Recurse -Force
        }
        $parentDir = Split-Path -Parent $Destination
        if (-not [string]::IsNullOrWhiteSpace($parentDir)) {
            New-Item -ItemType Directory -Force -Path $parentDir | Out-Null
        }
        Move-Item -LiteralPath $roots[0].FullName -Destination $Destination
        Set-Content -LiteralPath (Join-Path -Path $Destination -ChildPath $SnapshotMarker) -Value 'Filled from the project archive by install.ps1; replaced on the next run.' -Encoding ASCII
    } finally {
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-SourceDir {
    param([Parameter(Mandatory = $true)][string]$RequestedSourceDir)
    $RequestedSourceDir = Get-NormalizedDirectoryPath -Path $RequestedSourceDir

    # Running from a clone (or an extracted copy of the repository): use it as-is. -SourceDir and
    # CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR only say where a checkout is kept when this file runs
    # outside one, so a different directory named here is ignored exactly as install.sh ignores
    # it; say so rather than silently building something other than what was asked for.
    if ($InstallerPath) {
        $scriptDir = Split-Path -Parent $InstallerPath
        if (Test-Path -LiteralPath (Join-Path -Path $scriptDir -ChildPath $PatcherRelativePath) -PathType Leaf) {
            if ($Options.SourceDirRequested -and $RequestedSourceDir -ne (Get-NormalizedDirectoryPath -Path $scriptDir)) {
                Write-Host "Note: running from a clone; building $scriptDir and ignoring the requested source directory $RequestedSourceDir."
            }
            return $scriptDir
        }
    }

    $hasGit = Test-ApplicationCommand -Name 'git'
    if (Test-Path -LiteralPath (Join-Path -Path $RequestedSourceDir -ChildPath '.git')) {
        if (-not $hasGit) {
            Fail "$RequestedSourceDir is a Git checkout but Git is not installed. Install it with: winget install --id Git.Git -e (or choose another directory with CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR)."
        }
        $status = Get-NativeOutput -Command 'git' -Arguments @('-C', $RequestedSourceDir, 'status', '--porcelain')
        if ($status.ExitCode -ne 0) {
            Fail "git status failed in $RequestedSourceDir (exit code $($status.ExitCode))."
        }
        if (-not [string]::IsNullOrWhiteSpace($status.Output)) {
            Fail "$RequestedSourceDir has local changes; preserve or commit them before updating."
        }
        $branch = Get-NativeOutput -Command 'git' -Arguments @('-C', $RequestedSourceDir, 'branch', '--show-current')
        if ($branch.ExitCode -ne 0) {
            Fail "git branch --show-current failed in $RequestedSourceDir (exit code $($branch.ExitCode))."
        }
        if ($branch.Output -ne $SourceBranch) {
            Fail "$RequestedSourceDir is not on $SourceBranch; switch branches or set CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR."
        }
        Write-Log 'Updating source'
        Invoke-NativeCommand -Description 'git pull' -Command 'git' -Arguments @('-C', $RequestedSourceDir, 'pull', '--ff-only', 'origin', $SourceBranch)
    } elseif (Test-Path -LiteralPath (Join-Path -Path $RequestedSourceDir -ChildPath $SnapshotMarker)) {
        Save-SourceSnapshot -Destination $RequestedSourceDir
    } elseif (Test-Path -LiteralPath $RequestedSourceDir) {
        Fail "$RequestedSourceDir exists but is not a Git repository."
    } elseif (-not $hasGit) {
        Save-SourceSnapshot -Destination $RequestedSourceDir
    } else {
        Write-Log 'Downloading source'
        $parentDir = Split-Path -Parent $RequestedSourceDir
        if (-not [string]::IsNullOrWhiteSpace($parentDir)) {
            New-Item -ItemType Directory -Force -Path $parentDir | Out-Null
        }
        Invoke-NativeCommand -Description 'git clone' -Command 'git' -Arguments @('clone', '--depth', '1', '--branch', $SourceBranch, $RepositoryUrl, $RequestedSourceDir)
    }
    return $RequestedSourceDir
}

# ---------------------------------------------------------------------------------------------
# Existing installation.
# ---------------------------------------------------------------------------------------------
function Stop-InstallationProcess {
    <#
    Stops every process whose executable lives inside the installation directory (the launcher,
    the Electron processes, codex.exe and codex.real.exe) so the patcher can move the directory to
    a backup. Mirrors stop_bundle_processes in install.sh: up to ten attempts one second apart.
    #>
    param([Parameter(Mandatory = $true)][string]$InstallDir)

    $prefix = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        $foundProcess = $false
        foreach ($process in Get-Process) {
            # Path is $null for processes without a main module and throws for protected system
            # processes; neither can belong to a per-user installation.
            $processPath = $null
            try {
                $processPath = $process.Path
            } catch {
                $processPath = $null
            }
            if ([string]::IsNullOrEmpty($processPath)) {
                continue
            }
            if (-not $processPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                continue
            }
            $foundProcess = $true
            try {
                Stop-Process -Id $process.Id -Force -ErrorAction Stop
            } catch {
                # The process may have exited on its own; the next pass re-checks.
            }
        }
        if (-not $foundProcess) {
            return
        }
        Start-Sleep -Seconds 1
    }
    Fail "could not stop processes belonging to $InstallDir."
}

function Confirm-UntestedSource {
    <#
    The Codex app on this PC is a build the project has not recorded. Say what that means in plain
    words and ask. The patcher still refuses by itself if anything it expects has changed, and the
    official app is never modified, so continuing risks a router that behaves oddly, not a broken
    Codex. Returns when the answer is yes; fails otherwise.
    #>
    if ($Options.AllowUntestedSource) {
        return
    }
    Write-Host ''
    Write-Host 'The Codex app on this PC is newer than the versions this project has tested.'
    Write-Host 'Codex Router can still be built from it: every step checks that the app looks exactly'
    Write-Host 'as expected and stops by itself if it does not, and your Codex app is never changed.'
    Write-Host 'The only risk is that something in the router misbehaves on this newer version.'
    if (-not $Options.AssumeYes) {
        if (-not (Test-Interactive)) {
            Fail ("the Codex app is a build the project has not tested, and nobody can be asked here. " +
                "Set `$env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE = '1' (or pass -AllowUntestedSource) to continue anyway.")
        }
        $answer = Read-Host 'Build it anyway? [Y/n]'
        if ($answer -match '^\s*(n|no)\s*$') {
            Fail 'stopped at your request. Nothing was changed.'
        }
    }
}

# ---------------------------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------------------------
function Main {
    Write-Log 'Checking this PC'
    $toolchain = Assert-Prerequisite

    $projectDir = Resolve-SourceDir -RequestedSourceDir $Options.SourceDir
    Push-Location -LiteralPath $projectDir
    $previousIoEncoding = $env:PYTHONIOENCODING
    $previousOutputEncoding = $null
    try {
        # Invoke-NativeCommand hands npm and the patcher a pipe, not the console. PowerShell decodes
        # native output with [Console]::OutputEncoding, the OEM code page (cp437 on en-US); npm
        # writes UTF-8 to a pipe, and Python encodes a piped stdout with the ANSI code page and
        # errors='strict', so the patcher's progress lines come out garbled and a user-profile path
        # with a character outside the ANSI code page makes print() raise UnicodeEncodeError after
        # the copy has been built. PYTHONIOENCODING (rather than PYTHONUTF8, which would also change
        # the patcher's default file and subprocess encodings) makes Python write UTF-8, and the
        # console is switched to decode UTF-8. Both are restored in the finally block because they
        # would otherwise outlive the run in the user's `irm ... | iex` session.
        $env:PYTHONIOENCODING = 'utf-8'
        try {
            $previousOutputEncoding = [Console]::OutputEncoding
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        } catch {
            # SetConsoleOutputCP fails when the process has no console (a detached spawn). The
            # setting only affects how the output is displayed, not what is checked, so continue
            # with the encoding the host already had.
        }

        Write-Log 'Installing locked build tools'
        Invoke-NativeCommand -Description 'npm ci' -Command $toolchain.NpmPath -Arguments @('ci', '--ignore-scripts', '--no-audit', '--no-fund')

        $patchArguments = @()
        if ($Options.AllowUntestedSource) {
            $patchArguments += '--allow-untested-source'
        }
        if ($Options.NoDesktopShortcut) {
            $patchArguments += '--no-desktop-shortcut'
        }

        # Find out about the app before anything is stopped or built: an app build the project has
        # not recorded is a question for the person, and answering "no" must leave their running
        # copy alone.
        Write-Log 'Checking the Codex app on this PC'
        $checkArguments = @($toolchain.PythonArguments) + @($PatcherRelativePath, '--check-source') + @($patchArguments)
        $checkExitCode = Invoke-NativeCommandForExitCode -Description 'the source check' -Command $toolchain.PythonCommand -Arguments $checkArguments
        if ($checkExitCode -eq $UntestedSourceExitCode) {
            Confirm-UntestedSource
            $patchArguments += '--allow-untested-source'
        } elseif ($checkExitCode -ne 0) {
            Fail "the source check failed with exit code $checkExitCode; the message above says why."
        }

        if (Test-Path -LiteralPath $DestinationDir -PathType Container) {
            Write-Log 'Stopping the existing installation'
            Stop-InstallationProcess -InstallDir $DestinationDir
            $patchArguments += '--force'
        }

        Write-Log 'Building Codex Router (this takes a few minutes)'
        $patcherArguments = @($toolchain.PythonArguments) + @($PatcherRelativePath) + @($patchArguments)
        $commandLine = Format-CommandLine -Tokens (@($toolchain.PythonCommand) + @($patcherArguments))
        Write-Host "Running: $commandLine"
        Write-Host "     in: $projectDir"
        Invoke-NativeCommand -Description 'the Windows patcher' -Command $toolchain.PythonCommand -Arguments $patcherArguments
    } finally {
        # Assigning $null removes the variable when it was not set before.
        $env:PYTHONIOENCODING = $previousIoEncoding
        if ($null -ne $previousOutputEncoding) {
            try {
                [Console]::OutputEncoding = $previousOutputEncoding
            } catch {
                # Same condition as above; nothing was changed, so nothing needs restoring.
            }
        }
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
        Fail "the patcher finished without creating $Launcher."
    }

    if ($Options.NoLaunch) {
        Write-Log 'Skipping launch (NoLaunch requested)'
    } else {
        Write-Log 'Opening Codex Router'
        Start-Process -FilePath $Launcher
    }
    Write-Host ''
    Write-Host 'Codex Router is installed.'
    Write-Host '  Open it any time: press the Windows key, type "Codex Router" and press Enter.'
    if (-not $Options.NoDesktopShortcut) {
        Write-Host '  There is a Codex Router icon on your Desktop too.'
    }
    Write-Host '  Your normal Codex app is untouched and keeps working as before.'
    Write-Host "  Installed in: $DestinationDir"
    Write-Host "Installed successfully: $DestinationDir"
}

} # end of $installer

# Dot-sourcing with CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1 only defines the functions above, in the
# caller's scope, so the repository checks can exercise them without installing anything.
# Otherwise the definitions and Main run together in one anonymous scope that ends with the run
# (see the comment where the block starts).
if ($env:CODEX_SUBSCRIPTION_ROUTER_DRY_RUN -eq '1') {
    . $installer
} else {
    try {
        & { . $installer; Main }
    } finally {
        # Besides the param() variables (see the comment where the block starts), the block
        # variable is the only thing defined outside that scope; drop it as well.
        Remove-Variable -Name installer
    }
}
