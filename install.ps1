#Requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows_common.ps1')

try {
    Assert-WindowsX64
    Push-Location $PSScriptRoot
    try {
        $Root = $PSScriptRoot
        if ($Root -match '[^\x00-\x7F]') {
            throw 'Move this project to an ASCII path such as D:\VideoPrinterTurbo_LipSync (OpenCV/Conda path compatibility).'
        }
        # Catch incomplete Git/ZIP exports before downloading Conda or packages.
        # This PowerShell-only check also works before Python is installed.
        Assert-BundledSource -Root $Root
        $Prefix = Join-Path $Root '.conda\musetalk'
        Set-ProjectEnvironment -Root $Root -Prefix $Prefix
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        New-Item -ItemType Directory -Force -Path (Join-Path $Root 'deployment-records') | Out-Null

        $Nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
        if (-not $Nvidia) {
            throw 'Install the NVIDIA Windows driver first, then reopen PowerShell (nvidia-smi.exe is missing).'
        }
        Invoke-Checked -Executable $Nvidia

        $Conda = $null
        if ($env:CONDA_EXE -and (Test-Path -LiteralPath $env:CONDA_EXE)) {
            $Conda = $env:CONDA_EXE
        } else {
            $Conda = Get-Command conda -ErrorAction SilentlyContinue
        }
        if (-not $Conda) {
            $CondaRoot = Join-Path $Root '.tools\miniforge'
            $Conda = Join-Path $CondaRoot 'Scripts\conda.exe'
            if (-not (Test-Path -LiteralPath $Conda)) {
                $Downloads = Join-Path $Root '.cache\downloads'
                New-Item -ItemType Directory -Force -Path $Downloads | Out-Null
                $Installer = Join-Path $Downloads 'Miniforge3-26.7.2-1-Windows-x86_64.exe'
                $ExpectedHash = 'cda54a8d6d1d956a9ff6d497afc5abd9b0a655897e129a0e4d92a18966fd87e5'
                if (-not (Test-Path -LiteralPath $Installer) -or
                    (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
                    Write-Host 'Downloading Miniforge into this project...'
                    $Download = @{
                        UseBasicParsing = $true
                        Uri = 'https://github.com/conda-forge/miniforge/releases/download/26.7.2-1/Miniforge3-26.7.2-1-Windows-x86_64.exe'
                        OutFile = $Installer
                    }
                    # Windows PowerShell 5.1 does not automatically use HTTPS_PROXY.
                    if ($env:HTTPS_PROXY) { $Download.Proxy = $env:HTTPS_PROXY }
                    elseif ($env:HTTP_PROXY) { $Download.Proxy = $env:HTTP_PROXY }
                    Invoke-WebRequest @Download
                }
                if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
                    throw 'Miniforge SHA-256 mismatch. The installer was not executed; rerun to download it again.'
                }
                # NSIS requires /D to be the last argument, without enclosing quotes.
                $Setup = Start-Process -FilePath $Installer -ArgumentList "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$CondaRoot" -Wait -PassThru
                if ($Setup.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Conda)) {
                    throw "Miniforge installation failed (exit $($Setup.ExitCode)). Inspect $CondaRoot."
                }
            }
        }

        $Python = Join-Path $Prefix 'python.exe'
        if (-not (Test-Path -LiteralPath $Python)) {
            Invoke-Checked -Executable $Conda -Arguments @('create', '--prefix', $Prefix,
                '--override-channels', '-c', 'conda-forge', 'python=3.10', 'ffmpeg', 'vs2015_runtime', '-y')
        } else {
            Invoke-Checked -Executable $Python -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 10)')
            Invoke-Checked -Executable $Conda -Arguments @('install', '--prefix', $Prefix,
                '--override-channels', '-c', 'conda-forge', 'python=3.10', 'ffmpeg', 'vs2015_runtime', '-y')
        }
        Invoke-Checked -Executable $Python -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 10) and sys.maxsize > 2**32')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'pip', 'install', 'pip==24.0', 'setuptools==69.5.1', 'wheel==0.43.0')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'pip', 'install', '-c', 'deployment/constraints.txt',
            'torch==2.0.1', 'torchvision==0.15.2', 'torchaudio==2.0.2', '--index-url', 'https://download.pytorch.org/whl/cu118')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'pip', 'install', '-c', 'deployment/constraints.txt',
            'numpy==1.23.5', 'scipy==1.10.1')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'pip', 'install', '-c', 'deployment/constraints.txt',
            'mmcv==2.0.1', 'xtcocotools==1.14.3', '--only-binary=mmcv,xtcocotools',
            '-f', 'https://download.openmmlab.com/mmcv/dist/cu118/torch2.0/index.html')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'pip', 'install', '--no-build-isolation',
            '--only-binary=mmcv,xtcocotools,pycocotools', '-c', 'deployment/constraints.txt', '-r', 'deployment/requirements-inference.txt')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'pip', 'check')
        Invoke-Checked -Executable $Python -Arguments @('scripts/check_environment.py')
        Invoke-Checked -Executable $Python -Arguments @('-m', 'lipsync_service.model_assets')
        Invoke-Checked -Executable $Python -Arguments @('scripts/check_environment.py', '--load-model')

        # Use UTF-8 rather than PowerShell 5.1's default UTF-16 redirection.
        $Freeze = & $Python -m pip freeze
        if ($LASTEXITCODE -ne 0) { throw 'Could not export installed Python requirements.' }
        $Freeze | Set-Content -LiteralPath (Join-Path $Root 'deployment-records\requirements.installed.txt') -Encoding UTF8
        $Environment = & $Conda env export --prefix $Prefix
        if ($LASTEXITCODE -ne 0) { throw 'Could not export the Conda environment.' }
        $Environment | Set-Content -LiteralPath (Join-Path $Root 'deployment-records\environment.installed.yml') -Encoding UTF8
        Copy-Item -LiteralPath (Join-Path $Root 'third_party\MuseTalk.source.json') -Destination (Join-Path $Root 'deployment-records\MuseTalk.source.json') -Force
        Write-Host 'Installation and model initialization completed. Start with: .\start.bat'
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
