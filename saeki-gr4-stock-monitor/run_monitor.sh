#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [ -f "$HOME/.stock_monitor_env" ]; then
  source "$HOME/.stock_monitor_env"
fi

echo "========================================================================"
echo "세기몰 RICOH GR IV 재고 모니터를 시작합니다."
echo "종료하려면 이 터미널에서 Ctrl+C 를 누르세요."
echo "화면에 보이는 내용은 saeki_monitor_live.log 에도 같이 저장됩니다."
echo "========================================================================"
echo

caffeinate -dimsu python3 stock_monitor.py --interval 10 --open --sound-repeats 2 --backoff-minutes 10 2>&1 | tee -a saeki_monitor_live.log
