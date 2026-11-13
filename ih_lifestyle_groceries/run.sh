#!/bin/sh
# Options are in /data/options.json; the database is /data/ih.db (snapshots in /data/backup).
export IHA_DATA=/data
exec python3 /app/main.py
