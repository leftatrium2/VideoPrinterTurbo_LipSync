@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "VPT_EXIT=%ERRORLEVEL%"
if not "%VPT_EXIT%"=="0" pause
exit /b %VPT_EXIT%
