#!/bin/sh
# No host folders are mapped. Options are in /data/options.json; the database is /data/ih.db (snapshots in /data/backup).
# SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true); it is used only to read (and, with control: live, switch) input_boolean.<event>_mode.
export IHE_DATA=/data
export IHE_PORT=8819
export IHE_UI_PORT=8825
exec python3 /app/main.py
