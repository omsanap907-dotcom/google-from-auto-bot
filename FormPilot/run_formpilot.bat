@echo off
setlocal
cd /d "%~dp0"

echo Stopping any old FormPilot server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python(\.exe)?$' -and $_.CommandLine -match 'formpilot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo.
echo Starting THIS FormPilot copy...
python formpilot.py
if errorlevel 1 py formpilot.py
pause
