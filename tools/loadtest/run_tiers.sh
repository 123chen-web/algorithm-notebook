#!/bin/bash
# 阶梯压测：一档 2 分钟，档位 5 / 10 / 20 / 30 / 50 / 80 并发。
# 每档同时启动 monitor.py 采样目标进程资源。
# 用法：APP_PID=<uvicorn pid> bash tools/loadtest/run_tiers.sh
set -e
HOST="http://127.0.0.1:8001"
RESULTS="/tmp/loadtest/results"
VENV="/home/hatch/workspace/ouye/venv/bin"
mkdir -p "$RESULTS"

if [ -z "$APP_PID" ]; then
  echo "请先设置 APP_PID（uvicorn 进程 pid）"; exit 1
fi

for U in 5 10 20 30 50 80; do
  TAG=$(printf "tier_%02d" $U)
  echo "=== tier $U users (2min) ==="
  $VENV/python tools/loadtest/monitor.py "$APP_PID" /tmp/loadtest/uvicorn.log \
      "$RESULTS/${TAG}_resource.csv" 130 &
  MON_PID=$!
  LOADTEST_USERS=100 $VENV/locust -f tools/loadtest/locustfile.py \
      --headless -u $U -r $U -t 2m \
      --csv "$RESULTS/$TAG" --csv-full-history \
      --host "$HOST" 2>&1 | tail -25
  wait $MON_PID || true
  echo "--- tier $U done, cooling 20s ---"
  sleep 20
done
echo "ALL TIERS DONE"
