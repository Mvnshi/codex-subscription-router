<#
Tests for the functions in install.ps1 that decide things: the PATH refresh, which tools
are missing, how they get installed, the "newer Codex build" question, and where the source
comes from. Nothing is installed or downloaded: every test dot-sources install.ps1 in dry-run
mode into its own scope and replaces the commands that would touch the machine.

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-install.ps1
    pwsh -NoProfile -File scripts/test-install.ps1

Exits 1 if any test fails. CI runs it under both Windows PowerShell 5.1 and PowerShell 7.
#>
$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath '..\install.ps1')).Path
$script:count = 0
$script:failures = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    $script:count++
    $savedEnvironment = @{}
    foreach ($variable in 'CODEX_SUBSCRIPTION_ROUTER_DRY_RUN', 'CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES',
        'CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE', 'CODEX_SUBSCRIPTION_ROUTER_NO_DESKTOP_SHORTCUT',
        'CODEX_SUBSCRIPTION_ROUTER_ALLOW_ELEVATED', 'Path') {
        $savedEnvironment[$variable] = [Environment]::GetEnvironmentVariable($variable, 'Process')
    }
    try {
        $env:CODEX_SUBSCRIPTION_ROUTER_DRY_RUN = '1'
        & $Body
        Write-Host "ok    $Name"
    } catch {
        $script:failures++
        Write-Host "FAIL  $Name"
        Write-Host "      $($_.Exception.Message)"
    } finally {
        foreach ($key in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process')
        }
    }
}

function Assert-True { param($Condition, [string]$Message = 'expected true'); if (-not $Condition) { throw $Message } }
function Assert-Equal {
    param($Actual, $Expected, [string]$Message = '')
    if ("$Actual" -ne "$Expected") { throw "expected '$Expected' but got '$Actual' $Message" }
}
function Assert-Fails {
    param([scriptblock]$Body, [string]$Pattern)
    $message = $null
    try { & $Body } catch { $message = $_.Exception.Message }
    if ($null -eq $message) { throw "expected a failure matching '$Pattern' but nothing failed" }
    if ($message -notmatch $Pattern) { throw "failure '$message' does not match '$Pattern'" }
}

# ---------------------------------------------------------------------------------------------
Test-Case 'options come from switches and from environment variables' {
    $env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES = '1'
    $env:CODEX_SUBSCRIPTION_ROUTER_NO_DESKTOP_SHORTCUT = '1'
    . $installerPath
    Assert-True $Options.AssumeYes 'ASSUME_YES should enable AssumeYes'
    Assert-True $Options.NoDesktopShortcut 'NO_DESKTOP_SHORTCUT should enable NoDesktopShortcut'
    Assert-True (-not $Options.AllowUntestedSource) 'untested builds are not allowed by default'
}

Test-Case 'options are off by default' {
    foreach ($variable in 'ASSUME_YES', 'NO_DESKTOP_SHORTCUT', 'ALLOW_UNTESTED_SOURCE', 'ALLOW_ELEVATED') {
        [Environment]::SetEnvironmentVariable("CODEX_SUBSCRIPTION_ROUTER_$variable", $null, 'Process')
    }
    . $installerPath
    Assert-True (-not $Options.AssumeYes)
    Assert-True (-not $Options.NoDesktopShortcut)
    Assert-True (-not $Options.AllowElevated) 'an administrator shell must be refused unless CI says otherwise'
}

Test-Case 'an administrator shell is refused unless ALLOW_ELEVATED says it is a CI runner' {
    $env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_ELEVATED = '1'
    . $installerPath
    Assert-True $Options.AllowElevated
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    function Test-Elevated { return $true }
    function Test-WindowsHost { return $true }
    function Get-PrerequisiteStatus { throw 'reached the tool check' }
    # With the setting, the elevation check passes and the next step runs.
    Assert-Fails { Assert-Prerequisite } 'reached the tool check'
    # Without it, the installer stops at the elevation check.
    $Options.AllowElevated = $false
    Assert-Fails { Assert-Prerequisite } 'running as administrator'
}

Test-Case 'Update-SessionPath keeps the session entries, adds the registry ones, and repeats nothing' {
    . $installerPath
    $env:Path = 'C:\session-only;C:\Windows\System32;c:\WINDOWS\system32'
    Update-SessionPath
    $entries = @($env:Path -split ';')
    Assert-True ($entries -contains 'C:\session-only') 'the session-only entry must survive'
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $first = (($machine -split ';') | Where-Object { $_ } | Select-Object -First 1)
    Assert-True (@($entries | Where-Object { $_ -ieq $first }).Count -ge 1) 'a machine PATH entry must be present'
    $lower = @($entries | ForEach-Object { $_.ToLowerInvariant() })
    Assert-Equal @($lower | Select-Object -Unique).Count $lower.Count 'PATH has repeated entries'
}

Test-Case 'Get-PrerequisiteStatus names exactly what is missing' {
    . $installerPath
    function Test-ApplicationCommand { param([string]$Name); return ($Name -in @('go', 'npm')) }
    function Resolve-PythonInterpreter { return $null }
    $status = Get-PrerequisiteStatus
    Assert-Equal (($status.Missing | Sort-Object) -join ',') 'node,python'
    # Git is not a prerequisite.
    Assert-True ($status.Missing -notcontains 'git')
}

Test-Case 'Python that exists but is unusable counts as missing' {
    . $installerPath
    function Test-ApplicationCommand { param([string]$Name); return $true }
    function Resolve-PythonInterpreter { return [pscustomobject]@{ Command = ''; Reports = @("'python' is Python 3.8.0") } }
    Assert-Equal (Get-PrerequisiteStatus).Missing -join ',' 'python'
}

Test-Case 'nothing missing means nothing to install' {
    . $installerPath
    function Test-ApplicationCommand { param([string]$Name); return $true }
    function Resolve-PythonInterpreter { return [pscustomobject]@{ Command = 'python'; Reports = @() } }
    Assert-Equal (Get-PrerequisiteStatus).Missing.Count 0
}

Test-Case 'missing tools are installed with winget, each package once, when allowed' {
    $env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES = '1'
    . $installerPath
    function Write-Host { param($Object) }
    $script:calls = @()
    function Test-ApplicationCommand { param([string]$Name); return ($Name -eq 'winget') }
    function Invoke-NativeCommand { param($Description, $Command, $Arguments); $script:calls += , (@($Command) + @($Arguments)) }
    function Update-SessionPath { $script:refreshed = $true }
    function Write-Log { param($Message) }
    Install-MissingPrerequisite -Missing @('go', 'node', 'npm', 'python')
    Assert-Equal $script:calls.Count 3 'node and npm share one package'
    $ids = @($script:calls | ForEach-Object { $_[3] })
    Assert-Equal ($ids -join ',') 'GoLang.Go,OpenJS.NodeJS.LTS,Python.Python.3.12'
    foreach ($call in $script:calls) {
        Assert-Equal $call[0] 'winget'
        Assert-True ($call -contains '--accept-package-agreements') 'agreements must be accepted non-interactively'
        Assert-True ($call -contains '--silent')
    }
    Assert-True $script:refreshed 'PATH must be refreshed after installing'
}

Test-Case 'without winget the failure names what to install' {
    $env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES = '1'
    . $installerPath
    function Write-Host { param($Object) }
    function Test-ApplicationCommand { param([string]$Name); return $false }
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    Assert-Fails { Install-MissingPrerequisite -Missing @('go') } 'winget.*not found.*Go 1\.26'
}

Test-Case 'nobody to ask and no -Yes: nothing is installed and the way forward is named' {
    [Environment]::SetEnvironmentVariable('CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES', $null, 'Process')
    . $installerPath
    function Write-Host { param($Object) }
    $script:installed = $false
    function Test-ApplicationCommand { param([string]$Name); return ($Name -eq 'winget') }
    function Test-Interactive { return $false }
    function Invoke-NativeCommand { param($Description, $Command, $Arguments); $script:installed = $true }
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    Assert-Fails { Install-MissingPrerequisite -Missing @('node') } '(?s)CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES.*winget install --id OpenJS\.NodeJS\.LTS'
    Assert-True (-not $script:installed) 'nothing may be installed without an answer'
}

Test-Case 'answering no stops, answering yes or pressing Enter installs' {
    [Environment]::SetEnvironmentVariable('CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES', $null, 'Process')
    . $installerPath
    function Write-Host { param($Object) }
    $script:installs = 0
    function Test-ApplicationCommand { param([string]$Name); return ($Name -eq 'winget') }
    function Test-Interactive { return $true }
    function Invoke-NativeCommand { param($Description, $Command, $Arguments); $script:installs++ }
    function Update-SessionPath { }
    function Write-Log { param($Message) }
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    function Read-Host { param($Prompt); return $script:answer }
    $script:answer = 'n'
    Assert-Fails { Install-MissingPrerequisite -Missing @('go') } 'GoLang\.Go'
    Assert-Equal $script:installs 0
    foreach ($answer in 'y', '', 'Y', 'yes') {
        $script:answer = $answer
        Install-MissingPrerequisite -Missing @('go')
    }
    Assert-Equal $script:installs 4
}

Test-Case 'a newer Codex build: -AllowUntestedSource or -Yes goes ahead, nobody to ask stops, no stops' {
    . $installerPath
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    function Write-Host { param($Object) }
    # Already allowed: no question.
    $Options.AllowUntestedSource = $true
    Confirm-UntestedSource
    $Options.AllowUntestedSource = $false
    # -Yes answers it.
    $Options.AssumeYes = $true
    Confirm-UntestedSource
    $Options.AssumeYes = $false
    # No one to ask.
    function Test-Interactive { return $false }
    Assert-Fails { Confirm-UntestedSource } 'ALLOW_UNTESTED_SOURCE'
    # Someone to ask.
    function Test-Interactive { return $true }
    function Read-Host { param($Prompt); return $script:answer }
    $script:answer = 'n'
    Assert-Fails { Confirm-UntestedSource } 'Nothing was changed'
    $script:answer = ''
    Confirm-UntestedSource
}

Test-Case 'the patcher exit code for a newer build is the one the installer waits for' {
    . $installerPath
    Assert-Equal $UntestedSourceExitCode 3
    $python = (Get-Content -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath 'patch_app_windows.py') -Raw)
    Assert-True ($python -match 'UNTESTED_SOURCE_EXIT_CODE = 3') 'patch_app_windows.py must exit 3 too'
}

Test-Case 'without Git the source is a zip, and only a directory we filled is ever replaced' {
    . $installerPath
    $script:snapshots = @()
    function Test-ApplicationCommand { param([string]$Name); return ($Name -ne 'git') }
    function Save-SourceSnapshot { param([string]$Destination); $script:snapshots += $Destination }
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    $InstallerPath = ''
    $root = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('csr-test-' + [guid]::NewGuid().ToString('N'))
    try {
        # missing directory -> fill it
        $missing = Join-Path -Path $root -ChildPath 'source'
        Resolve-SourceDir -RequestedSourceDir $missing | Out-Null
        Assert-Equal $script:snapshots.Count 1
        # a directory with our marker -> refill it
        New-Item -ItemType Directory -Force -Path $missing | Out-Null
        Set-Content -LiteralPath (Join-Path -Path $missing -ChildPath $SnapshotMarker) -Value 'x'
        Resolve-SourceDir -RequestedSourceDir $missing | Out-Null
        Assert-Equal $script:snapshots.Count 2
        # someone else's directory -> refused, not replaced
        $foreign = Join-Path -Path $root -ChildPath 'foreign'
        New-Item -ItemType Directory -Force -Path $foreign | Out-Null
        Set-Content -LiteralPath (Join-Path -Path $foreign -ChildPath 'mine.txt') -Value 'keep'
        Assert-Fails { Resolve-SourceDir -RequestedSourceDir $foreign } 'not a Git repository'
        Assert-True (Test-Path -LiteralPath (Join-Path -Path $foreign -ChildPath 'mine.txt')) 'the foreign directory must be untouched'
        # a Git checkout with no Git -> a clear message, not a snapshot over it
        $checkout = Join-Path -Path $root -ChildPath 'checkout'
        New-Item -ItemType Directory -Force -Path (Join-Path -Path $checkout -ChildPath '.git') | Out-Null
        Assert-Fails { Resolve-SourceDir -RequestedSourceDir $checkout } 'Git checkout but Git is not installed'
        Assert-Equal $script:snapshots.Count 2
    } finally {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case 'Save-SourceSnapshot unpacks the archive into place, marks it, and replaces it next time' {
    . $installerPath
    function Fail { param([string]$Reason); throw "FAIL: $Reason" }
    function Write-Log { param($Message) }
    $root = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('csr-test-' + [guid]::NewGuid().ToString('N'))
    try {
        # An archive shaped like GitHub's: one top-level folder holding the project.
        $build = Join-Path -Path $root -ChildPath 'build'
        $project = Join-Path -Path $build -ChildPath 'codex-subscription-router-main'
        New-Item -ItemType Directory -Force -Path (Join-Path -Path $project -ChildPath 'scripts') | Out-Null
        Set-Content -LiteralPath (Join-Path -Path $project -ChildPath 'scripts\patch_app_windows.py') -Value 'version one'
        $zip = Join-Path -Path $root -ChildPath 'main.zip'
        Compress-Archive -LiteralPath $project -DestinationPath $zip
        function Invoke-WebRequest { param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile); Copy-Item -LiteralPath $zip -Destination $OutFile }
        $destination = Join-Path -Path $root -ChildPath 'out\source'
        Save-SourceSnapshot -Destination $destination
        Assert-Equal (Get-Content -LiteralPath (Join-Path -Path $destination -ChildPath 'scripts\patch_app_windows.py')) 'version one'
        Assert-True (Test-Path -LiteralPath (Join-Path -Path $destination -ChildPath $SnapshotMarker)) 'the marker must be written'
        # Next run: a changed archive replaces the old files entirely.
        Set-Content -LiteralPath (Join-Path -Path $destination -ChildPath 'stale.txt') -Value 'stale'
        Set-Content -LiteralPath (Join-Path -Path $project -ChildPath 'scripts\patch_app_windows.py') -Value 'version two'
        Remove-Item -LiteralPath $zip
        Compress-Archive -LiteralPath $project -DestinationPath $zip
        Save-SourceSnapshot -Destination $destination
        Assert-Equal (Get-Content -LiteralPath (Join-Path -Path $destination -ChildPath 'scripts\patch_app_windows.py')) 'version two'
        Assert-True (-not (Test-Path -LiteralPath (Join-Path -Path $destination -ChildPath 'stale.txt'))) 'stale files must go'
        # An archive that is not this project is refused before anything is replaced.
        $other = Join-Path -Path $root -ChildPath 'other\something-else'
        New-Item -ItemType Directory -Force -Path $other | Out-Null
        Set-Content -LiteralPath (Join-Path -Path $other -ChildPath 'readme.txt') -Value 'no'
        Remove-Item -LiteralPath $zip
        Compress-Archive -LiteralPath $other -DestinationPath $zip
        Assert-Fails { Save-SourceSnapshot -Destination $destination } 'does not look like this project'
        Assert-Equal (Get-Content -LiteralPath (Join-Path -Path $destination -ChildPath 'scripts\patch_app_windows.py')) 'version two'
    } finally {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ''
Write-Host "$($script:count - $script:failures) of $($script:count) install.ps1 tests passed"
if ($script:failures -ne 0) { exit 1 }
