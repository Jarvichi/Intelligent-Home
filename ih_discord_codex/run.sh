#!/bin/sh
set -eu
export IHB_PORT=8815
export IH_UI_PORT=8812
export IHB_DATA=/data
export CODEX_HOME=/config/codex-home
export CODEX_SQLITE_HOME=/config/codex-home
export CODEX_WORKSPACE=/config/workspace/ih

python3 /app/ui_server.py >/dev/null 2>&1 &
ui_pid=$!
python3 /app/main.py &
worker_pid=$!
trap 'kill -TERM "$worker_pid" "$ui_pid" 2>/dev/null || true' INT TERM

if wait "$worker_pid"; then
    result=0
else
    result=$?
fi
kill "$ui_pid" 2>/dev/null || true
wait "$ui_pid" 2>/dev/null || true
exit "$result"
