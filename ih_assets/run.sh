#!/bin/sh
# No host folders are mapped. Options are in /data/options.json; the database is /data/ih.db (snapshots in /data/backup).
# SUPERVISOR_TOKEN is used only in mode "live", to publish sensor.assets_low and sensor.assets_high.
export IHA_DATA=/data
export IHA_PORT=8850
export IHA_UI_PORT=8851
exec python3 /app/main.py
