#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHS_PORT=8795
export IHS_DATA=/data
exec python3 /app/main.py
