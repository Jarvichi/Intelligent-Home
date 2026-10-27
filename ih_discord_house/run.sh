#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHH_PORT=8793
export IHH_DATA=/data
exec python3 /app/main.py
