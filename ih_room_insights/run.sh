#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
export IHRI_DATA=/data
export IHRI_PORT=8812
export IHRI_UI_PORT=8813
exec python3 /app/main.py
