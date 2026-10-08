#!/bin/bash
# Judge0 1.13.1 /api worker overlay; preserve scheduler/worker signal handling.
set -eo pipefail
cd /api
source ./scripts/load-config
export RAILS_LOG_TO_STDOUT=
sudo install -m 600 /dev/null /api/environment
export | sudo tee /api/environment >/dev/null

running=1
worker_pid=0
scheduler_pid=0
stop_workers() {
    running=0
    if ((worker_pid > 0)); then
        # Resque children stop gracefully; no process IDs or job data are logged.
        pkill -QUIT -P "$worker_pid" 2>/dev/null || true
        kill -TERM "$worker_pid" 2>/dev/null || true
    fi
    if ((scheduler_pid > 0)); then
        kill -TERM "$scheduler_pid" 2>/dev/null || true
    fi
}
trap stop_workers TERM INT
mkdir -p tmp/pids
while ((running)); do
    if ! kill -0 "$scheduler_pid" 2>/dev/null || ((scheduler_pid == 0)); then
        rake resque:scheduler >/dev/null 2>&1 &
        scheduler_pid=$!
    fi
    rm -f tmp/pids/resque.pid
    echo 'OY runner workers starting.'
    rails resque:workers >/dev/null 2>&1 &
    worker_pid=$!
    if ((!running)); then
        stop_workers
    fi
    wait "$worker_pid" || true
    if ((running)); then
        echo 'OY runner workers stopped; retrying.' >&2
        sleep 2
    fi
done
wait "$scheduler_pid" 2>/dev/null || true
