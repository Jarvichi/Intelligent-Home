#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. No Home Assistant token is needed.
export IHB_PORT=8803
export IHB_DATA=/data
exec python3 /app/main.py
