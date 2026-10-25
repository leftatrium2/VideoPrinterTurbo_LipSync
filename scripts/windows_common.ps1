# Shared by the native Windows installer and launcher (PowerShell 5.1+).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-WindowsX64 {
    $Architecture = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $Architecture = $env:PROCESSOR_ARCHITEW6432 }
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess -or
        $Architecture -ne 'AMD64') {
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

function Assert-BundledSource {
    param([Parameter(Mandatory = $true)][string] $Root)
    $ManifestPath = Join-Path $Root 'third_party/MuseTalk.source.json'
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw 'MuseTalk source manifest is missing. Re-sync the complete project.'
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $Vendor = Join-Path $Root 'third_party/MuseTalk'
    $Missing = @()
    $Changed = @()
    $Count = 0
    foreach ($Entry in $Manifest.files.PSObject.Properties) {
        $Count++
        $SourceFile = Join-Path $Vendor $Entry.Name
        if (-not (Test-Path -LiteralPath $SourceFile -PathType Leaf)) {
            $Missing += $Entry.Name
        } elseif ((Get-FileHash -LiteralPath $SourceFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.Value) {
            $Changed += $Entry.Name
        }
    }
    if ($Count -eq 0) { throw 'MuseTalk source manifest contains no files. Re-sync the complete project.' }
    if ($Missing.Count -gt 0 -or $Changed.Count -gt 0) {
        throw "MuseTalk bundled source is incomplete or changed. Missing: $($Missing -join ', '); modified: $($Changed -join ', '). Re-sync third_party/MuseTalk, including musetalk/models/*.py."
    }
    Write-Host "MuseTalk bundled source verified ($Count files)."
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
