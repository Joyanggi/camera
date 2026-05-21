@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PY_CMD=py -3"
) else (
  set "PY_CMD=python"
)

%PY_CMD% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3을 찾지 못했습니다.
  echo https://www.python.org/downloads/windows/ 에서 Python을 설치한 뒤 다시 실행하세요.
  pause
  exit /b 1
)

%PY_CMD% -c "import requests, bs4, plyer" >nul 2>nul
if errorlevel 1 (
  echo 필요한 Python 패키지를 설치합니다...
  %PY_CMD% -m pip install --user -r requirements.txt
  if errorlevel 1 (
    echo 패키지 설치에 실패했습니다.
    pause
    exit /b 1
  )
)

echo ========================================================================
echo 카메라 재고 모니터를 시작합니다.
echo 종료하려면 이 터미널에서 Ctrl+C 를 누르세요.
echo Slack 알림을 쓰려면 같은 폴더의 .env 파일에 SLACK_WEBHOOK_URL을 넣으세요.
echo ========================================================================
echo.

%PY_CMD% stock_monitor.py --interval 10 --open --sound-repeats 3 --backoff-minutes 10

echo.
echo 모니터가 종료되었습니다.
pause
