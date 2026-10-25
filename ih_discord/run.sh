#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHD_PORT=8791
export IHD_DATA=/data
exec python3 /app/main.py
