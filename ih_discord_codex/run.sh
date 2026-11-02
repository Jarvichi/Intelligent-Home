#!/bin/sh
set -eu
export IHB_PORT=8804
export IHB_DATA=/data
export CODEX_HOME=/config/codex-home
export CODEX_SQLITE_HOME=/config/codex-home
export CODEX_WORKSPACE=/config/workspace/ih
exec python3 /app/main.py
