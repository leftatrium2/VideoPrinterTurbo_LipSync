@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "VPT_EXIT=%ERRORLEVEL%"
if not "%VPT_EXIT%"=="0" echo Installation failed. See the error above.
pause
exit /b %VPT_EXIT%
