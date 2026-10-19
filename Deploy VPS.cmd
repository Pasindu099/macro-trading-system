@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy_vps.ps1"
if errorlevel 1 (
  echo.
  echo Deployment stopped. Read the error above.
  pause
  exit /b 1
)
echo.
echo Deployment complete.
pause
