#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHW_DATA=/data
export IHW_PORT=8802
python3 /app/ui_server.py >/dev/null 2>&1 &
exec python3 /app/main.py
