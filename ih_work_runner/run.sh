#!/bin/sh
# Options are in /data/options.json. SUPERVISOR_TOKEN is set by the Supervisor (homeassistant_api: true).
# The Claude login/MCP settings live in /data/claude-home (the add-on's own volume), never in the image.
export IHWR_DATA=/data
export IHWR_PORT=8847
export IHWR_UI_PORT=8099
mkdir -p /data/claude-home
exec python3 /app/runner.py
