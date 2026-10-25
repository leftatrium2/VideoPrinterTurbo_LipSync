@echo off
setlocal
set "VPT_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "VPT_POWERSHELL=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%VPT_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "VPT_EXIT=%ERRORLEVEL%"
if not "%VPT_EXIT%"=="0" pause
exit /b %VPT_EXIT%
