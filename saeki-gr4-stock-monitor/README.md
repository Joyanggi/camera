# Saeki GR IV Stock Monitor

세기몰 `RICOH GR IV` 상품 1개만 확인하는 재고 모니터입니다.

## macOS

```bash
cd saeki-gr4-stock-monitor
./run_monitor.sh
```

## Windows

```bat
cd saeki-gr4-stock-monitor
run_monitor_windows.bat
```

## Slack

`.env.example`을 `.env`로 복사한 뒤 `SLACK_WEBHOOK_URL`을 넣으면 재고 감지 시 Slack 알림이 전송됩니다.

## Test

```bash
python3 stock_monitor.py --once --sound-repeats 0
python3 stock_monitor.py --test-alert --sound-repeats 0
```
