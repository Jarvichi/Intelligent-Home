# Intelligent Home Jobs (ih_jobs)

A small Home Assistant add-on that runs two hourly jobs and writes the results as Home Assistant sensors:

- **backupstore**: `sensor.backup_storage_local` and (if you name the two drive sensors) `sensor.backup_storage_drive`: used GB, backups versus other files, free space.
- **metoffice**: `sensor.met_office_forecast` and `sensor.weather_forecast_best` from the Met Office DataHub (UK) with a fallback to your Home Assistant weather entity.

Nothing about your home is in the code: location comes from your Home Assistant home zone, everything else from the add-on **options**. It maps no folders and starts in **dry-run** (logs "would set ...", changes nothing).

## Going live
A job acts only when `mode` is `live`, the job is in `live_jobs`, `old_loops_disabled` is on (you stopped any older script doing the same), and `/data/STOP_LIVE` does not exist (kill switch). `set_live.py live|dry` flips the options in one step (needs `SUPERVISOR_TOKEN`).
Jobs run when the scheduler sends a signed message (every hour) and once 90 seconds after start.

## Options (examples are fake; see `options.example.json`)
| Option | Meaning | Example |
|---|---|---|
| `mode` | `dry` or `live` | `"dry"` |
| `live_jobs` | Jobs allowed to act | `["backupstore","metoffice"]` |
| `old_loops_disabled` | You switched older scripts off | `false` |
| `met_office_key` | Met Office DataHub API key (secret) | |
| `drive_used_entity`, `drive_total_entity` | Your two Google Drive storage sensors; empty = no drive sensor | `"sensor.drive_used"`, `"sensor.drive_total"` |
| `timezone` | Day boundaries for the forecast (default `Europe/London`, the Met Office area) | `"Europe/London"` |
| `zone_entity` | Zone whose latitude/longitude is used | `"zone.home"` |
| `fallback_weather_entity` | Weather entity used when Met Office data is missing or stale | `"weather.forecast_home"` |
| `ingest_url`, `ingest_token` | Status page add-on (default host is the Supervisor gateway `172.30.32.1`, port 8790) | |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | Scheduler bus | `core-mosquitto`, `1883` |
| `enable_scheduler`, `signing_key` | Receive scheduled runs (needs a signing key) | `false` |

The output sensor names are this module's fixed contract (documented above).

## Sources
`schedule_client.py` and `manifest.json` are COPIES made by `sync_from_service.sh`.

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder.
