# Shared by the native Windows installer and launcher (PowerShell 5.1+).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-WindowsX64 {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess -or
        $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'Use 64-bit PowerShell on Windows x64 with an NVIDIA GPU.'
    }
}

function Invoke-Checked {
    param([Parameter(Mandatory = $true)] $Executable, [string[]] $Arguments = @())
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

function Set-ProjectEnvironment {
    param([string] $Root, [string] $Prefix)
    $env:CONDA_PKGS_DIRS = Join-Path $Root '.cache\conda'
    $env:PIP_CACHE_DIR = Join-Path $Root '.cache\pip'
    $env:HF_HOME = Join-Path $Root '.cache\huggingface'
    $env:TORCH_HOME = Join-Path $Root '.cache\torch'
    $env:XDG_CACHE_HOME = Join-Path $Root '.cache'
    $env:MPLCONFIGDIR = Join-Path $Root '.cache\matplotlib'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:TEMP = Join-Path $Root '.runtime\tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    # Direct Python execution still needs Conda's DLL and executable directories.
    $env:PATH = "$Prefix;$(Join-Path $Prefix 'Library\bin');$(Join-Path $Prefix 'Scripts');$env:PATH"
}
