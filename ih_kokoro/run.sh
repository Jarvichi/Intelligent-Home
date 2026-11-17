#!/bin/sh
# Options are in /data/options.json (read by server.py). Wyoming server on 0.0.0.0:10210.
exec python3 /app/server.py --uri tcp://0.0.0.0:10210
