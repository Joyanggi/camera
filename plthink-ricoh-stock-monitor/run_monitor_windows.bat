@echo off
setlocal
chcp.com 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PY_CMD=py -3"
) else (
  set "PY_CMD=python"
)

%PY_CMD% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3 was not found.
  echo Install Python 3 from https://www.python.org/downloads/windows/ and run again.
  pause
  exit /b 1
)

%PY_CMD% -c "import requests, bs4, plyer" >nul 2>nul
if errorlevel 1 (
  echo Installing required Python packages...
  %PY_CMD% -m pip install --user -r requirements.txt
  if errorlevel 1 (
    echo Failed to install Python packages.
    pause
    exit /b 1
  )
)

echo ========================================================================
echo Plthink RICOH stock monitor starting.
echo Press Ctrl+C in this terminal to stop.
echo For Slack alerts, put SLACK_WEBHOOK_URL in the .env file.
echo ========================================================================
echo.

%PY_CMD% stock_monitor.py --interval 10 --open --sound-repeats 3 --backoff-minutes 10

echo.
echo Monitor stopped.
pause
