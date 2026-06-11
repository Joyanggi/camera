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

## 웹 대시보드 (aggregator + web)

전체 모니터를 모아 `status.json`을 만들고 정적 페이지로 보여주는 구조입니다.

- **aggregator/check_all.py** — 3개 모니터 폴더의 check 함수를 import해 한 번에 실행, 결과를 루트 `status.json`에 저장. 이전 상태와 비교해 `BUYABLE` 전환 시 Slack 알림.
- **.github/workflows/check_stock.yml** — 10분마다 cron 실행, `status.json` 변경분 자동 커밋. `SLACK_WEBHOOK_URL`은 레포 Secrets에 등록.
- **web/** — Vercel에 배포할 정적 프론트엔드 (`index.html`, `app.js`, `style.css`). `raw.githubusercontent.com`에서 `status.json`을 받아 60초마다 새로고침.

### 배포 순서

1. **GitHub Secrets**에 `SLACK_WEBHOOK_URL` 추가 (Settings → Secrets and variables → Actions)
2. **Actions 권한 확인**: Settings → Actions → General → Workflow permissions → "Read and write permissions" 선택
3. **Vercel** 프로젝트 생성: 이 레포 import → Root Directory `web` 지정 → 배포
4. 결과 URL은 `https://<프로젝트명>.vercel.app/`. `noindex, nofollow` + `X-Robots-Tag`로 검색엔진 차단되어 있어 링크 아는 사람만 접근 가능.

### 로컬에서 한 번 돌려보기

```bash
pip install -r aggregator/requirements.txt
python aggregator/check_all.py
```

