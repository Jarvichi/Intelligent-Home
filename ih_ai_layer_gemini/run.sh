#!/bin/sh
# No host folders are mapped. Options are in /data/options.json.
export IHA_DATA=/data
exec python3 /app/main.py
