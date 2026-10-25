#Requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows_common.ps1')

try {
    Assert-WindowsX64
    Push-Location $PSScriptRoot
    try {
        Assert-BundledSource -Root $PSScriptRoot
        $Prefix = Join-Path $PSScriptRoot '.conda\musetalk'
        $Python = Join-Path $Prefix 'python.exe'
        if (-not (Test-Path -LiteralPath $Python)) { throw 'Run install.bat first.' }
        Set-ProjectEnvironment -Root $PSScriptRoot -Prefix $Prefix
        $env:LIPSYNC_ENGINE = 'musetalk'
        Remove-Item Env:LIPSYNC_COMMAND -ErrorAction SilentlyContinue
        if (-not $env:LIPSYNC_STORAGE) { $env:LIPSYNC_STORAGE = Join-Path $PSScriptRoot 'storage' }
        $BindAddress = if ($env:LIPSYNC_HOST) { $env:LIPSYNC_HOST } else { '127.0.0.1' }
        $Port = if ($env:LIPSYNC_PORT) { $env:LIPSYNC_PORT } else { '8081' }
        Invoke-Checked -Executable $Python -Arguments @('-m', 'uvicorn', 'app:app', '--host', $BindAddress,
            '--port', $Port, '--workers', '1', '--timeout-graceful-shutdown', '4200')
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
