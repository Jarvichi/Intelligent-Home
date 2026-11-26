#!/bin/sh
# Options: /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (hassio_api + homeassistant_api).
export IHWD_DATA=/data
exec python3 /app/main.py
