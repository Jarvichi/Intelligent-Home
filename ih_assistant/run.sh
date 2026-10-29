#!/bin/sh
# No host folders are mapped. Options are in /data/options.json; every persona keeps its own folder under /data/personas/<key>/.
export IHP_DATA=/data
export IHP_PORT=8830
exec python3 /app/main.py
