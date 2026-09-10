<#
Codex Subscription Router - Windows installer.

This is the Windows counterpart of install.sh: it downloads or updates the
source checkout, installs the locked build tools, builds the independent copy
of the official desktop app with scripts\patch_app_windows.py, and launches it.
The official installation is only read; it is never modified.

Two ways to run it:

  1. The documented one-liner. Invoke-Expression cannot pass parameters, so
     every option is also read from an environment variable (set them in the
     same PowerShell session before running the line):

       $env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR = 'D:\src\codex-subscription-router'  # optional
       $env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE = '1'                       # optional
       $env:CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH = '1'                                   # optional
       irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.ps1 | iex

  2. From a clone or a downloaded copy, with ordinary parameters:

       powershell -ExecutionPolicy Bypass -File .\install.ps1 [-SourceDir <path>] [-AllowUntestedSource] [-NoLaunch]

  Environment variables are honoured in both styles. -SourceDir wins over the
  variable when both are given; the switches are enabled by either form.

  CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1 makes dot-sourcing this file define the
  functions without running the installation (used by the repository checks).
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$SourceDir,
    [switch]$AllowUntestedSource,
    [switch]$NoLaunch
)

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
# The environment variables are the documented contract; [Environment]::GetFolderPath is the same
# source Windows fills them from and only serves as a fallback for sessions that lack them.
$UserProfileDir = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
$LocalAppDataDir = if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { $env:LOCALAPPDATA } else { [Environment]::GetFolderPath('LocalApplicationData') }
$DefaultSourceDir = Join-Path -Path $UserProfileDir -ChildPath '.codex-subscription-router-mvnshi\source'
$DestinationDir = Join-Path -Path $LocalAppDataDir -ChildPath 'Programs\Codex Subscription Router'
$Launcher = Join-Path -Path $DestinationDir -ChildPath 'Codex Subscription Router.exe'
$PatcherRelativePath = 'scripts\patch_app_windows.py'
$MinimumNodeVersion = [version]'22.12.0'
$MinimumGoVersion = [version]'1.26.0'
$MinimumPythonVersion = [version]'3.11.0'

# Options are captured into an ordinary variable here because, under Invoke-Expression, the
# param() variables are not visible to the functions defined below; only variables assigned at
# this top level are. Main is therefore also called from this top level (see the end of the file).
$Options = [pscustomobject]@{
    SourceDir = if (-not [string]::IsNullOrWhiteSpace($SourceDir)) {
        $SourceDir
    } elseif (-not [string]::IsNullOrWhiteSpace($env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR)) {
        $env:CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR
    } else {
        $DefaultSourceDir
    }
    AllowUntestedSource = ($AllowUntestedSource.IsPresent -or ($env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE -eq '1'))
    NoLaunch = ($NoLaunch.IsPresent -or ($env:CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH -eq '1'))
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
    # installation, and the session stays open.
    throw "Install failed: $Reason"
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
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    & $Command @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Fail "$Description failed with exit code $LASTEXITCODE."
    }
}

function Get-NativeOutput {
    <#
    Runs a native command and returns its standard output (trimmed) with the exit code. Standard
    error is intentionally not redirected: in Windows PowerShell 5.1 a redirected stderr line
    becomes an error record, which $ErrorActionPreference = 'Stop' would turn into an exception
    unrelated to the exit code. Left alone it simply reaches the console.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    $lines = @(& $Command @Arguments)
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

function Assert-Prerequisite {
    <#
    Fails closed on anything the build needs. Returns the resolved toolchain (which Python
    invocation to use) so Main does not probe twice. The official desktop installation itself is
    not checked here: its Windows layout is discovered and verified by scripts\patch_app_windows.py
    with exact checks, and duplicating a guess here would only weaken that.
    #>
    if (-not (Test-WindowsHost)) {
        Fail 'Codex Subscription Router supports Windows only.'
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

    $missing = @()
    foreach ($commandName in @('git', 'go', 'node', 'npm')) {
        if (-not (Test-ApplicationCommand -Name $commandName)) {
            $missing += $commandName
        }
    }
    $python = Resolve-PythonInterpreter
    if ($null -eq $python) {
        $missing += 'python'
    }
    if ($missing.Count -ne 0) {
        Fail "missing prerequisites: $($missing -join ' '). Install Git for Windows, Go 1.26+, Node.js 22.12+ (with npm), and Python 3.11+, then rerun this command."
    }

    $nodeProbe = Get-NativeOutput -Command 'node' -Arguments @('-p', 'process.versions.node')
    $nodeVersion = ConvertTo-Version -Text $nodeProbe.Output
    if ($nodeProbe.ExitCode -ne 0 -or $null -eq $nodeVersion) {
        Fail "could not determine the Node.js version ('node -p process.versions.node' exited with code $($nodeProbe.ExitCode))."
    }
    if ($nodeVersion -lt $MinimumNodeVersion) {
        Fail "Node.js 22.12 or newer is required; found v$nodeVersion."
    }

    $goProbe = Get-NativeOutput -Command 'go' -Arguments @('env', 'GOVERSION')
    $goVersion = ConvertTo-Version -Text $goProbe.Output
    if ($goProbe.ExitCode -ne 0 -or $null -eq $goVersion) {
        Fail "could not determine the Go version ('go env GOVERSION' exited with code $($goProbe.ExitCode))."
    }
    if ($goVersion -lt $MinimumGoVersion) {
        Fail "Go 1.26 or newer is required; found $($goProbe.Output)."
    }

    if ([string]::IsNullOrWhiteSpace($python.Command)) {
        Fail "Python 3.11 or newer is required; $($python.Reports -join '; ')."
    }
    Write-Host "Using Python $($python.Version) via '$($python.Display)' ($($python.Path))."

    return [pscustomobject]@{
        PythonCommand = $python.Command
        PythonArguments = [string[]]$python.Arguments
        PythonDisplay = $python.Display
        NodeVersion = $nodeVersion
        GoVersion = $goVersion
    }
}

# ---------------------------------------------------------------------------------------------
# Source checkout (same rules as install.sh).
# ---------------------------------------------------------------------------------------------
function Resolve-SourceDir {
    param([Parameter(Mandatory = $true)][string]$RequestedSourceDir)

    # Running from a clone (or an extracted copy of the repository): use it as-is.
    if ($InstallerPath) {
        $scriptDir = Split-Path -Parent $InstallerPath
        if (Test-Path -LiteralPath (Join-Path -Path $scriptDir -ChildPath $PatcherRelativePath) -PathType Leaf) {
            return $scriptDir
        }
    }

    if (Test-Path -LiteralPath (Join-Path -Path $RequestedSourceDir -ChildPath '.git')) {
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
    } elseif (Test-Path -LiteralPath $RequestedSourceDir) {
        Fail "$RequestedSourceDir exists but is not a Git repository."
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

# ---------------------------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------------------------
function Main {
    Write-Log 'Checking this PC'
    $toolchain = Assert-Prerequisite

    $projectDir = Resolve-SourceDir -RequestedSourceDir $Options.SourceDir
    Push-Location -LiteralPath $projectDir
    try {
        Write-Log 'Installing locked build tools'
        Invoke-NativeCommand -Description 'npm ci' -Command 'npm' -Arguments @('ci', '--ignore-scripts', '--no-audit', '--no-fund')

        $patchArguments = @()
        if (Test-Path -LiteralPath $DestinationDir -PathType Container) {
            Write-Log 'Stopping the existing installation'
            Stop-InstallationProcess -InstallDir $DestinationDir
            $patchArguments += '--force'
        }
        if ($Options.AllowUntestedSource) {
            $patchArguments += '--allow-untested-source'
        }

        Write-Log 'Building Codex Subscription Router'
        $patcherArguments = @($toolchain.PythonArguments) + @($PatcherRelativePath) + @($patchArguments)
        $commandLine = Format-CommandLine -Tokens (@($toolchain.PythonCommand) + @($patcherArguments))
        Write-Host "Running: $commandLine"
        Write-Host "     in: $projectDir"
        Invoke-NativeCommand -Description 'the Windows patcher' -Command $toolchain.PythonCommand -Arguments $patcherArguments
    } finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
        Fail "the patcher finished without creating $Launcher."
    }

    if ($Options.NoLaunch) {
        Write-Log 'Skipping launch (NoLaunch requested)'
    } else {
        Write-Log 'Launching Codex Subscription Router'
        Start-Process -FilePath $Launcher
    }
    Write-Host ''
    Write-Host "Installed successfully: $DestinationDir"
}

# Dot-sourcing with CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1 only defines the functions above so the
# repository checks can exercise them without installing anything.
if ($env:CODEX_SUBSCRIPTION_ROUTER_DRY_RUN -ne '1') {
    Main
}
