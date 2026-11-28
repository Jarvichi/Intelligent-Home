#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHPS_DATA=/data
export IHPS_PORT=8813
export IHPS_UI_PORT=8811
exec python3 /app/main.py
