# Intelligent Home

## What it does

The hub of Intelligent Home. It shows a status page for the other add-ons and services, runs an optional signed MQTT scheduler, and has a settings hub that fills shared keys of the other add-ons from one list. It also lists every add-on of the repository in a sidebar manager. Parent of every other Intelligent Home add-on.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home**.
2. Set `ingest_token` to a long random text. Other add-ons use it to push their status.
3. Start the add-on and open **Intelligent Home** in the sidebar.
4. Optional: turn on `provision` to let the settings hub fill shared keys of the other add-ons (see More details).

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `ingest_token` | Yes | Bearer token modules use to push (secret) |
| `enable_broker` | No | Secrets broker (parked, keep off). Default: `false`. |
| `enable_scheduler` | No | Turn the scheduler on. Default: `false`. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `default_timezone` | No | Time zone for daily/weekly jobs whose manifest has no `tz` (IANA name). Default: `UTC`. |
| `module_keys` | No | One `{module, key}` per module for message signing |
| `provision` | No | Settings hub, see below. Default: `false`. |
| `provision_overwrite` | No | Settings hub, see below. Default: `false`. |
| `provision_interval_min` | No | Settings hub, see below. Default: `10`. |
| `shared` | No | Settings hub, see below |

## Troubleshooting

- **Other add-ons do not appear.** They must push with the same `ingest_token`; check their `ingest_url`.
- **Scheduler does nothing.** It is off until `enable_scheduler` is true and the `mqtt_*` options and `module_keys` are set.
- **Settings hub changes nothing.** Turn on `provision`, set `ingest_token`, restart, then read `/provision/status`. It only fills empty options unless `provision_overwrite` is on.

## Privacy

Nothing leaves your network. Status data and tokens stay on the add-on; the only outbound target is the MQTT broker you configure.

## More details

### Settings hub
Fills the empty options of your other `ih_*` add-ons from one list, so there are no hand-copied keys.
1. Set `ingest_token`, then turn on `provision` and restart this add-on.
2. On first start it imports the options your installed `ih_*` add-ons already hold into `/data/hub.json` (same value everywhere = global; otherwise per add-on).
3. Add anything missing to `shared`: name `opt` (all add-ons) or `<addon_suffix>.opt` (one add-on, e.g. `ih_ai_layer_groq.api_key`). `shared` wins over hub.json.
4. Every `provision_interval_min` minutes it fills EMPTY options and restarts changed add-ons that are running. `provision_overwrite` also corrects differing values.
5. See names only (never values) on the status page "Settings" section, or `GET /provision/status` (bearer `ingest_token`). `POST /provision` runs now, `POST /provision/import` re-imports.
It only touches add-ons from its own repository. A `register_key` is generated once.

### Add-on manager (0.6.0)

Sidebar panel "Intelligent Home" (Supervisor ingress, port 8789, answers only the ingress gateway). Lists every add-on of `app/catalogue.json` (written by `export_repo.py`) with install state, version, CPU, memory, last seen, missing settings, and what each one needs or is needed by. Buttons: Install, Start, Stop, Restart, Update, Logs. Acts only on add-ons of this repository. Install and update run as background jobs; after an install the provisioner runs once.
