#!/bin/sh
# No host folders are mapped. Options are in /data/options.json.
export IHA_DATA=/data
python3 /app/ui_server.py >/dev/null 2>&1 &
exec python3 /app/main.py
