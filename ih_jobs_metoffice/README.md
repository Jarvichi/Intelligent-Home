# Intelligent Home - Jobs - Met Office (ih_jobs_metoffice)

A child of the **Intelligent Home - Jobs** add-on (`ih_jobs`). It runs the hourly forecast job and writes:

- `sensor.met_office_forecast` and `sensor.weather_forecast_best` from the Met Office DataHub (UK), with a fallback to your Home Assistant weather entity when the Met Office data is missing or older than 3 hours.

Location comes from your Home Assistant home zone. It starts in **dry-run** (the forecast is fetched for real, but Home Assistant states are only logged as "would set ...") and maps no folders.

## How it joins the parent
Give this add-on and the parent the **same** `register_key` (any long random text). Every 30 seconds the child posts a signed registration to the parent (`parent_url`). The parent lists it (`GET /children`) and forwards scheduled runs to it. The child keeps working if the parent is stopped (it runs once 90 s after start, and every `own_timer_min` minutes if above 0). With no `register_key` the link is off. The API is never called more than once per 55 minutes (shared cache in `/data`).

## Going live
Acts only when `mode` is `live`, `old_loops_disabled` is on and `/data/STOP_LIVE` does not exist.

## Options (examples are fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `mode` | `dry` or `live` |
| `old_loops_disabled` | You switched older scripts off |
| `parent_url` | The parent add-on (default: Supervisor gateway, port 8792) |
| `callback_url` | How the parent reaches this child (empty = Supervisor gateway and this add-on's port 8799) |
| `register_key` | Shared secret with the parent (secret) |
| `own_timer_min` | Run by itself every N minutes (0 = only when the parent or start-up asks) |
| `met_office_key` | Met Office DataHub API key (secret) |
| `timezone` | Day boundaries (default `Europe/London`) |
| `zone_entity` | Zone whose latitude/longitude is used |
| `fallback_weather_entity` | Weather entity used when Met Office data is missing or stale |
| `log_payloads` | false = each job run (scheduler, start-up, own timer or parent trigger) logs ONE line with metadata only (run id, trigger, job, chars of job log, ms, ok, why). true = the line also holds the first 200 chars of the trigger and of the last job log line (one line, newlines escaped). Keys and tokens are never logged. |

## Boot and watchdog
Default `boot: manual`. For automatic start set the Supervisor option "Start on boot" and add `watchdog: http://[HOST]:[PORT:8799]/healthz` to `config.yaml` after the dry-run looks right.

## Sources
`jobs_link.py`, `jobs_child.py`, `manifest.json` and `jobs/` are COPIES made by `sync_from_service.sh`.

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder.
