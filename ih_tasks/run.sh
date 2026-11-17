#!/bin/sh
# No host folders are mapped. Options are in /data/options.json; the database is /data/ih.db (snapshots in /data/backup).
# SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true); it is used only by the optional to-do list mirror.
export IHT_DATA=/data
export IHT_PORT=8818
export IHT_UI_PORT=8823
exec python3 /app/main.py
