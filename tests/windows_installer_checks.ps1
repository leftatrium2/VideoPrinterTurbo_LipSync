# Executable helper checks on PowerShell 5.1+; no GPU, Conda or installer downloads.
param([Parameter(Mandatory = $true)][string] $Root,
      [Parameter(Mandatory = $true)][string] $Workspace)
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'scripts/windows_common.ps1')
Assert-BundledSource -Root $Root

foreach ($ScriptName in @('install.ps1', 'start.ps1', 'scripts/windows_common.ps1', 'scripts/smoke_musetalk.ps1')) {
    $Tokens = $null
    $ParseErrors = $null
    $Tree = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Root $ScriptName), [ref]$Tokens, [ref]$ParseErrors)
    if ($ParseErrors.Count -gt 0) { throw ($ParseErrors | Out-String) }
    if ($ScriptName -eq 'install.ps1') {
        $Calls = $Tree.FindAll({ param($Node) $Node -is [System.Management.Automation.Language.CommandAst] }, $true)
        $Preflight = @($Calls | Where-Object { $_.GetCommandName() -eq 'Assert-BundledSource' })
        if ($Preflight.Count -ne 1) { throw 'Installer must check bundled source exactly once before installation.' }
        foreach ($Call in $Calls) {
            if ($Call.GetCommandName() -in @('Invoke-Checked', 'Invoke-WebRequest', 'Start-Process') -and
                $Call.Extent.StartOffset -lt $Preflight[0].Extent.StartOffset) {
                throw 'Source preflight must run before downloads, package installation or native setup.'
            }
        }
    }
}

function Expect-Failure {
    param([scriptblock] $Action, [string] $Pattern)
    $Failed = $false
    try { & $Action } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw }
        $Failed = $true
    }
    if (-not $Failed) { throw "Expected failure matching: $Pattern" }
}

if ([Environment]::Is64BitProcess) {
    $SavedOS = $env:OS
    $SavedArchitecture = $env:PROCESSOR_ARCHITECTURE
    $SavedNativeArchitecture = $env:PROCESSOR_ARCHITEW6432
    try {
        $env:OS = 'Windows_NT'
        $env:PROCESSOR_ARCHITECTURE = 'x86'
        $env:PROCESSOR_ARCHITEW6432 = 'AMD64'
        Assert-WindowsX64
        $env:PROCESSOR_ARCHITEW6432 = 'ARM64'
        Expect-Failure { Assert-WindowsX64 } '64-bit PowerShell'
    } finally {
        $env:OS = $SavedOS
        $env:PROCESSOR_ARCHITECTURE = $SavedArchitecture
        $env:PROCESSOR_ARCHITEW6432 = $SavedNativeArchitecture
    }
}

$Fixture = Join-Path $Workspace 'project with spaces'
New-Item -ItemType Directory -Force -Path $Fixture | Out-Null
Expect-Failure { Assert-BundledSource -Root $Fixture } 'source manifest'
$Vendor = Join-Path $Fixture 'third_party/MuseTalk'
$Source = Join-Path $Vendor 'musetalk/models/vae.py'
New-Item -ItemType Directory -Force -Path (Split-Path $Source -Parent) | Out-Null
[IO.File]::WriteAllBytes($Source, [Text.Encoding]::UTF8.GetBytes("class VAE: pass`n"))
$Hash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant()
$Manifest = @{ files = @{ 'musetalk/models/vae.py' = $Hash } } | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $Fixture 'third_party/MuseTalk.source.json'), $Manifest)
Assert-BundledSource -Root $Fixture
Remove-Item -LiteralPath $Source
Expect-Failure { Assert-BundledSource -Root $Fixture } 'missing.*musetalk/models/vae.py'
[IO.File]::WriteAllBytes($Source, [Text.Encoding]::UTF8.GetBytes('changed'))
Expect-Failure { Assert-BundledSource -Root $Fixture } 'modified.*musetalk/models/vae.py'

$Self = Join-Path $PSHOME 'pwsh'
if ($env:OS -eq 'Windows_NT') {
    $Self = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path -LiteralPath $Self)) { $Self = Join-Path $PSHOME 'pwsh.exe' }
}
Expect-Failure { Invoke-Checked -Executable $Self -Arguments @('-NoProfile', '-NonInteractive', '-Command', 'exit 17') } 'exit 17'
Write-Host 'Windows installer helper checks passed.'
