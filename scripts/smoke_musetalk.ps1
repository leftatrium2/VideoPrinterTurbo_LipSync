#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Video,
    [Parameter(Mandatory = $true)][string] $Audio,
    [Parameter(Mandatory = $true)][string] $Output,
    [ValidateSet('strict', 'audio')][string] $Timeline = 'strict',
    [ValidateRange(1, 100)][int] $Repeat = 2
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows_common.ps1')

try {
    Assert-WindowsX64
    $Root = Split-Path $PSScriptRoot -Parent
    Push-Location $Root
    try {
        Assert-BundledSource -Root $Root
        $Prefix = Join-Path $Root '.conda\musetalk'
        $Python = Join-Path $Prefix 'python.exe'
        if (-not (Test-Path -LiteralPath $Python)) { throw 'Run install.bat first.' }
        Set-ProjectEnvironment -Root $Root -Prefix $Prefix
        Invoke-Checked -Executable $Python -Arguments @('scripts/smoke_musetalk.py',
            '--video', $Video, '--audio', $Audio, '--output', $Output,
            '--timeline', $Timeline, '--repeat', [string]$Repeat)
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "MuseTalk verification failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
