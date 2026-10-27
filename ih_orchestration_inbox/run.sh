#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. No Home Assistant token is needed.
export IHI_PORT=8800
export IHI_DATA=/data
exec python3 /app/main.py
