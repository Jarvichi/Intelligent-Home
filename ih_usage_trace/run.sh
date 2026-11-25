#!/bin/sh
# No host folders are mapped. Options are in /data/options.json; the database is /data/ih.db (snapshots in /data/backup).
# SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true); it is used only to read the usage sensors.
export IHU_DATA=/data
export IHU_PORT=8848
export IHU_UI_PORT=8849
exec python3 /app/main.py
