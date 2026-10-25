"""Run real PowerShell helper checks where a PowerShell executable is available."""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_powershell_installer_helpers(tmp_path):
    executable = shutil.which('pwsh') or shutil.which('powershell.exe')
    portable = ROOT / '.tools/powershell/pwsh'
    if not executable and portable.is_file():
        executable = str(portable)
    if not executable:
        pytest.skip('PowerShell is required for installer helper checks')
    env = dict(os.environ, DOTNET_CLI_HOME=str(ROOT / '.cache/dotnet'),
               DOTNET_SYSTEM_GLOBALIZATION_INVARIANT='1', POWERSHELL_TELEMETRY_OPTOUT='1',
               XDG_CACHE_HOME=str(ROOT / '.cache'), XDG_CONFIG_HOME=str(ROOT / '.cache/config'),
               PSModuleAnalysisCachePath=str(ROOT / '.cache/powershell/ModuleAnalysisCache'))
    result = subprocess.run([executable, '-NoLogo', '-NoProfile', '-NonInteractive',
                             '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tests/windows_installer_checks.ps1'),
                             '-Root', str(ROOT), '-Workspace', str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Windows installer helper checks passed.' in result.stdout


def test_environment_failure_diagnostics_show_bounded_log_tail(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location('check_environment', ROOT / 'scripts/check_environment.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log = tmp_path / 'worker.log'
    log.write_text('old diagnostic\n' + 'progress\n' * 100 + 'ModuleNotFoundError: musetalk.models\n', encoding='utf-8')
    module.print_worker_log(log, lines=3)
    output = capsys.readouterr().err
    assert 'ModuleNotFoundError: musetalk.models' in output
    assert 'old diagnostic' not in output
    module.print_worker_log(tmp_path / 'absent.log')
    assert 'unavailable' in capsys.readouterr().err.lower()
