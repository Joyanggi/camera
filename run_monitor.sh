#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "========================================================================"
echo "카메라 재고 모니터를 시작합니다."
echo "종료하려면 이 터미널에서 Ctrl+C 를 누르세요."
echo "화면에 보이는 내용은 stock_monitor_live.log 에도 같이 저장됩니다."
echo "========================================================================"
echo

caffeinate -dimsu python3 stock_monitor.py --interval 35 --open --sound-repeats 2 2>&1 | tee -a stock_monitor_live.log
