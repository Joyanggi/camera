# 카메라 재고 입고 확인 자동화

## mac-camera-stock-monitor

맥북에서 실행하는 버전입니다.

```bash
cd mac-camera-stock-monitor
./run_monitor.sh
```

Slack 알림은 `$HOME/.stock_monitor_env`에 `SLACK_WEBHOOK_URL`을 설정해서 사용합니다.

## windows-camera-stock-monitor

윈도우 VSCode 또는 배치파일로 실행하는 버전입니다.

```bat
cd windows-camera-stock-monitor
run_monitor_windows.bat
```

Slack 알림은 `.env.example`을 `.env`로 복사한 뒤 `SLACK_WEBHOOK_URL`을 넣어서 사용합니다.
