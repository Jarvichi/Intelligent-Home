#!/bin/sh
# Maps /share (the notes live in /share/brain). Options are in /data/options.json.
export IHA_DATA=/data
exec python3 /app/main.py
