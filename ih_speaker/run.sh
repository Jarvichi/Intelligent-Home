#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHS_DATA=/data
export IHS_PORT=8831
exec python3 /app/main.py
