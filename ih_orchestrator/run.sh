#!/usr/bin/with-contenv sh
# No host folders are mapped. Status arrives by POST /ingest; options (ingest_token, enable_broker, enable_scheduler, mqtt_*, module_keys) are in /data/options.json.
export IH_PORT=8790
export IH_DATA=/data
exec python3 /app/main.py
