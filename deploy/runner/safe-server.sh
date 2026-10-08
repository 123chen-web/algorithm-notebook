#!/bin/bash
# Judge0 1.13.1 /api startup overlay. Keep secrets and submission data out of logs.
set -eo pipefail
cd /api
source ./scripts/load-config
export RAILS_LOG_TO_STDOUT=
sudo install -m 600 /dev/null /api/environment
export | sudo tee /api/environment >/dev/null

for ((attempt=1; attempt<=RESTART_MAX_TRIES; attempt++)); do
    rm -f tmp/pids/server.pid
    # Rails/Isolate raw diagnostics can include payloads; never send them to Docker logs.
    if rails db:create db:migrate db:seed >/dev/null 2>&1; then
        echo 'OY runner server starting.'
        if rails s -b 0.0.0.0 >/dev/null 2>&1; then
            exit 0
        fi
    fi
    echo 'OY runner server stopped; retrying.' >&2
    sleep 2
done
echo 'OY runner server startup failed; inspect configuration offline.' >&2
exit 1
