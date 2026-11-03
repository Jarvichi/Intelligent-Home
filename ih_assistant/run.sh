#!/bin/sh
# No host folders are mapped. Options are in /data/options.json; every persona keeps its own folder under /data/personas/<key>/.
export IHP_DATA=/data
export IHP_PORT=8830
python3 /app/ui_server.py >/dev/null 2>&1 &
exec python3 /app/main.py
