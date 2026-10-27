#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. No Home Assistant token is needed.
export IHT_PORT=8797
export IHT_DATA=/data
exec python3 /app/main.py
