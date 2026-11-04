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
4. The **Traces** tab shows correlated Discord AI requests when the bot has an `ingest_token`. It stores component names, safe operation/status codes and timings only; no request or response content. Entries expire after 7 days and are capped at 5,000 spans.
5. Optional: turn on `provision` to let the settings hub fill shared keys of the other add-ons (see More details).

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
| `fact_clients` | No | Setting `fact_clients`. |

## Consumer progress

The Health tab shows content-free outcome progress for IH Assistant direct messages: idle, waiting, catching up, stalled, disabled, or unknown. It distinguishes a live service heartbeat from completed replies. Stale or inconsistent data is shown as unknown. The monitor does not send, replay, restart, or notify. Its durable checkpoint is stored under `/data` and is included in normal add-on backups.

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
3. Add anything missing to `shared`: name `opt` (all add-ons) or `<addon_suffix>.opt` (one add-on, e.g. `ih_ai_layer.groq_key`). `shared` wins over hub.json.
4. Every `provision_interval_min` minutes it fills EMPTY options and restarts changed add-ons that are running. `provision_overwrite` also corrects differing values.
5. See names only (never values) on the status page "Settings" section, or `GET /provision/status` (bearer `ingest_token`). `POST /provision` runs now, `POST /provision/import` re-imports.
It only touches add-ons from its own repository. A `register_key` is generated once.

### Add-on manager (0.6.0)

Sidebar panel "Intelligent Home" (Supervisor ingress, port 8789, answers only the ingress gateway). Lists every add-on of `app/catalogue.json` (written by `export_repo.py`) with install state, version, CPU, memory, last seen, missing settings, and what each one needs or is needed by. Buttons: Install, Start, Stop, Restart, Update, Logs. Acts only on add-ons of this repository. Install and update run as background jobs; after an install the provisioner runs once.

### Add-on status contributions (0.10.10)

An IH add-on may add `ui_contribution` to its existing authenticated `POST /ingest` body. It must push a matching `source`, manifest and status row with that name. The v1 contribution is read-only JSON:

```json
{"version":1,"state":"ok","summary":"Dry-run mode; 2 built-in runs","last_activity_at":1000,"metrics":[{"label":"Built-in runs","value":2}],"next_step":"Check the Jobs view"}
```

`state` is `ok`, `attention` or `unknown`; it describes the add-on's work, while service health remains a separate Manager check. `last_activity_at` is a Unix timestamp or `null`. Supply at most four numeric metrics. Keep the summary, next step and labels short and free of secrets, names, URLs and HTML. Orchestrator validates and stores only these fields. Manager labels a contribution stale after 120 seconds without a push and stops showing its metrics after six hours. Add-ons without a contribution continue to show central health and Supervisor state. Jobs is the first producer; richer applications can retain their own sidebar UI.

### Prebuilt image (0.9.1)
Each add-on card shows "Prebuilt image: yes (tag X)" or "no (built on the Pi)". The answer comes from `app/catalogue.json` (`image: true/false`, written by the export from `tools/prebuilt.txt`); it says the published repo offers a ready image, not that this install has pulled it. The tag is the catalogue version.

### Request traces (0.10.0)
The manager accepts metadata-only trace batches at `POST /trace`, authenticated with the existing `ingest_token`. The Discord AI Bot submits one trace for each accepted request: bot intake, AI Layer call, AI Layer/provider spans, and Discord transport reply. The trace API rejects free-form message fields and stores only bounded identifiers, component/operation names, status/reason codes, timings and parent relationships. Traces remain on the Orchestrator's `/data`, expire after 7 days, and are capped at 5,000 spans. The ingress manager exposes recent traces at `/api/traces` and individual timelines at `/api/trace/<trace_id>`.

### Usage (0.8.0)

The page tab **Usage** and the line "Used: N today (M failed), last HH:MM" on each Add-ons card show which add-ons are really being used. The background refresher reads the recent log of each installed add-on every 5 minutes and counts invocation lines (`request id=` for `ih_ai_layer`, `ask id=` for the AI providers, `invoke id=` for jobs, games, cooking, discord and the orchestrator itself, and any line with `ok=True` / `ok=False` for other add-ons). The formats are in the table `USAGE_FORMATS` in `app/manager.py`; add a row for a new one.

- Only counts and times are stored or shown, never log text. Counts are kept per day (14 days) in `/data/usage.json`, so they survive log rotation and restarts; a line is never counted twice.
- Logs hold only a limited tail, so counts start when this version first ran. Log lines have a time but no date; a quiet spell longer than a day can date very old lines a day late.
- "Not used in 7 days" is highlighted in the Usage tab: those add-ons are candidates to stop or remove.
