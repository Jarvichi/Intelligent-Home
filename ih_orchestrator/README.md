# Intelligent Home (ih_orchestrator)

A status page and a small scheduler for a set of Home Assistant add-ons and services ("modules").

- **Status page**: modules (and an optional host helper) push their manifest and health results with `POST /ingest` (bearer token). `GET /` shows a page, `/status` JSON, `/sensor` a flat JSON for a Home Assistant REST sensor.
- **Scheduler** (off by default): sends signed MQTT messages that tell a module to run a job. Each message is HMAC-signed with a key per module (`module_keys`).
- **Secrets broker**: parked (off).

It maps no folders and holds no secrets in code. Prepared as a local add-on: copy this folder to your local add-ons share, reload the add-on store, install. Tmux/file checks need the host helper `host_probe.py` (outside the add-on); the add-on itself only runs http checks.

## Options (examples are fake; see `options.example.json`)
| Option | Meaning | Example |
|---|---|---|
| `ingest_token` | Bearer token modules use to push (secret) | |
| `enable_broker` | Secrets broker (parked, keep off) | `false` |
| `enable_scheduler` | Turn the scheduler on | `false` |
| `mqtt_host`, `mqtt_port` | Message bus (default host is the standard Mosquitto add-on) | `core-mosquitto`, `1883` |
| `mqtt_user`, `mqtt_password` | Bus login | |
| `default_timezone` | Time zone for daily/weekly jobs whose manifest has no `tz` (IANA name) | `"UTC"` |
| `module_keys` | One `{module, key}` per module for message signing | `[{"module":"my_module","key":"..."}]` |
| `log_payloads` | false = each scheduler dispatch, each job done message, each broker register/admin call and each pause/resume call logs ONE line with metadata only (id, source, what, chars in/out, ms, ok, why). Status pushes (/ingest), heartbeats and health checks are not logged. true = the line also holds the first 200 chars of the request and reply (topic, secret NAMES, never values, signatures, codes or PINs; /admin never logs text). One line, newlines escaped. | `false` |

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder.
