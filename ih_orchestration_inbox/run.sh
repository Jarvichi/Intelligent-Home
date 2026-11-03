#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. No Home Assistant token is needed.
export IHI_PORT=8800
export IHI_DATA=/data
python3 /app/ui_server.py >/dev/null 2>&1 &
exec python3 /app/main.py
