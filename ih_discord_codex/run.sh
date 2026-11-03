#!/bin/sh
set -eu
export IHB_PORT=8815
export IHB_DATA=/data
export CODEX_HOME=/config/codex-home
export CODEX_SQLITE_HOME=/config/codex-home
export CODEX_WORKSPACE=/config/workspace/ih
python3 /app/ui_server.py >/dev/null 2>&1 &
exec python3 /app/main.py
