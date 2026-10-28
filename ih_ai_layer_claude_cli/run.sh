#!/bin/sh
# No host folders are mapped. Options are in /data/options.json. The Claude login lives in /data/claude-home (the add-on's own volume).
export IHA_DATA=/data
mkdir -p /data/claude-home
exec python3 /app/main.py
