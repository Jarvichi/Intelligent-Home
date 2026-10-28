# Intelligent Home - Jobs

## What it does

Parent of the Jobs family; lists and triggers the child job add-ons. Hourly Home Assistant jobs: backup storage sensors and the Met Office forecast. Dry-run by default; each job goes live only when told to (live_jobs) after any older loop is switched off. Parent of its family.

## Requires

- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Jobs**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `met_office_key`, `ingest_token`, `register_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `live_jobs` | No | Jobs allowed to act |
| `old_loops_disabled` | No | You switched older scripts off. Default: `false`. |
| `met_office_key` | Yes | Met Office DataHub API key (secret) |
| `drive_used_entity` | No | Your two Google Drive storage sensors; empty = no drive sensor |
| `drive_total_entity` | No | Your two Google Drive storage sensors; empty = no drive sensor |
| `timezone` | No | Day boundaries for the forecast (default `Europe/London`, the Met Office area) |
| `zone_entity` | No | Zone whose latitude/longitude is used. Default: `zone.home`. |
| `fallback_weather_entity` | No | Weather entity used when Met Office data is missing or stale. Default: `weather.forecast_home`. |
| `ingest_url` | No | Status page add-on (default host is the Supervisor gateway `172.30.32.1`, port 8790) |
| `ingest_token` | Yes | Status page add-on (default host is the Supervisor gateway `172.30.32.1`, port 8790) |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_jobs`. |
| `mqtt_password` | No | MQTT password for this add-on. |
| `enable_scheduler` | No | Receive scheduled runs (needs a signing key). Default: `false`. |
| `signing_key` | No | Receive scheduled runs (needs a signing key) |
| `register_key` | Yes | Shared secret. Must be the same value in this add-on and its parent. Empty = link off. |
| `run_builtin_jobs` | No | Run the built-in jobs in this add-on until the child add-ons take over. Default: `true`. |
| `disk_total_entity` | No | Optional sensor with total disk size. |
| `disk_used_entity` | No | Optional sensor with used disk size. |
| `host_info` | No | Ask the Supervisor for disk size. Switch off if it is refused. Default: `true`. |
| `discord_token` | No | Optional Discord bot token for plugins that post. |
| `plugins` | No | Extra job plugins to enable. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

Everything stays on your network, except optional calls to the services you give keys for (for example the Met Office). No personal data is sent.

## More details

### Parent and children (0.3.0)
This add-on is the **parent**, "Intelligent Home - Jobs". Each job can also run as its own child add-on: **Intelligent Home - Jobs - Backup** (`ih_jobs_backup`, port 8798) and **Intelligent Home - Jobs - Met Office** (`ih_jobs_metoffice`, port 8799). Children are separate processes.
- Set the same `register_key` here and in each child. Each child posts a signed registration every 30 s (`POST /register`, HMAC-SHA256 of the body, 5-minute freshness window). Empty key = link off, every registration refused.
- `GET /children` lists the children and their last status. `POST /trigger` (signed, body `{"job": "metoffice"}`) runs a job now.
- When the scheduler fires a job and its child is registered and fresh (seen in the last 2 minutes), the parent forwards the run to the child. Otherwise it runs its own built-in copy.
- `run_builtin_jobs` (default on, same as 0.2.x): turn it off once the children are live, so only the children act.
- New backupstore options `disk_total_entity`, `disk_used_entity`, `host_info`: see the child README (the disk size no longer makes the job fail).
- Boot: `boot: manual`. To auto-start set "Start on boot" and add `watchdog: http://[HOST]:[PORT:8792]/healthz` to `config.yaml` after the dry-run looks right.

### Going live
A job acts only when `mode` is `live`, the job is in `live_jobs`, `old_loops_disabled` is on (you stopped any older script doing the same), and `/data/STOP_LIVE` does not exist (kill switch). `set_live.py live|dry` flips the options in one step (needs `SUPERVISOR_TOKEN`).
Jobs run when the scheduler sends a signed message (every hour) and once 90 seconds after start.

### Plugins (0.4.0)
Drop a file in `app/plugins/` exposing `NAME`, `SCHEDULE` (`{"kind":"daily","at":"19:30"}` or `{"kind":"every","minutes":30}`), `DEFAULTS`, `run(ctx)`. Turn it on with the option `plugins`: a list of `{name, live, settings}`; `settings` is a JSON text with that plugin's house values. Dry by default (`live: false`: logs what it would do). `/data/STOP_LIVE` or `/data/STOP_<name>` forces dry. State lives in `/data/<name>/`. `/status` shows mode, last run, last error and next run per plugin. Shared helpers: `app/ihlib` (a copy: edit `ihlib/` and run `sync_ihlib.sh`).

First plugin `physio_reminder`: settings `user_id`, `presence_entity`, `exercises` (list), `at`, `pick`, `openers`, `closers`, `token_option`; bot token in option `discord_token`.

Plugin `birthday_reminder`: settings `user_id`, `at` (default 18:00), `leads` (default [7, 3]), `birthdays` (list of `{name, date: "YYYY-MM-DD" or "MM-DD", group}`), `group_labels`, `token_option`. One DM groups all birthdays of that day; age is shown when the year is known.

##### Car plugins (0.5.0)

`car_log` (every 10 minutes):

```
car_log: every 10 minutes, log electric-car charge sessions with their cost. Each rise of the battery percentage since the last run is costed at the
CURRENT electricity rate, so a session that crosses a cheap-rate boundary is priced right. A session ends when the car is unplugged, the battery has not risen
for `stall_min` minutes, or the charge target is reached. Wall energy = battery energy / efficiency (an estimate: there is no charger meter).
Every house value comes from settings (JSON in plugins[].settings):
  soc_entity       battery percentage sensor
  plug_entity      plug sensor; plugged when its state (any case) equals plug_value
  plug_value       the plug_entity state meaning 'cable locked in' (default CHARGING_CABLE_LOCKED)
  plug2_entity     optional second plug sensor; when it has a real state, it wins: plugged when its state equals plug2_value
  plug2_value      default CONNECTED
  target_entity    optional charge-target percentage sensor (session ends on reaching it)
  rate_entity      current electricity rate sensor (money per kWh)
  battery_kwh      usable battery size (default 39.0)
  efficiency       default 0.88
  stall_min        default 60
  offpeak_rate     a rate below this counts as off-peak (default 0.20)
  minutes          run every N minutes (default 10)
  publish_entity   optional: write a summary (month and week cost, last sessions) to this entity id via the HA state API (live only)
State (plugin state 'charges'): {"sessions": [...], "cur": null or {...}, "last_soc": number}. Same shape as the old script's json, so it can be copied in.
Dry: reads the sensors and logs what it would record; saves nothing.
```

`car_tyres` (every 10 minutes):

```
car_tyres: warn one person by Discord DM when a car tyre is low, with a picture. Runs every 10 minutes but acts only inside a daytime window.
A tyre is LOW when its pressure is below `low_fraction` of its own target pressure. Only COLD tyres count: the odometer must not have changed for
`parked_hours`. Cooldown `cooldown_days` between warnings, unless a low tyre dropped `drop_psi` or more since the last warning.
Every house value comes from settings (JSON in plugins[].settings):
  user_id               Discord user id to DM
  token_option          add-on option holding the bot token (default discord_token)
  pressure_entity       entity id of ONE tyre's pressure sensor with the position replaced by {pos}, e.g. sensor.car_tire_pressure_{pos}
  target_entity         same for the target pressure, e.g. sensor.car_tire_pressure_target_{pos}   (both sensors in kPa)
  positions             {"front_left": "front left", ...}: key = {pos} in the entity ids, value = words used in the message
  odometer_entity       sensor that changes while the car is driven
  picture_url           optional URL of a picture (PNG) showing which tyres are low; fetched fresh at send time. NOTE: the old script re-rendered it first.
  recipient_name, car_name, signoff   words used in the message ("Hi <recipient_name>. The <car_name>'s tyres ...", "- <signoff>")
  window_start, window_end   hours of the day when it may send (default 8 and 20)
  low_fraction (0.9), parked_hours (2), cooldown_days (3), drop_psi (2)
  ignore_gates          true = ignore window, parked check and cooldown (use with a manual dry run to see the text now)
State (plugin state 'tyres'): {"last_sent", "next_ok", "last_psi": {pos: psi}, "hold_until"}. Same shape as the old script's json.
Dry: logs the message it would send; no DM, no picture fetch, no state change.
```

`car_charge` (every 10 minutes):

```
car_charge: one evening heads-up by Discord DM about the electric car: it looks at tomorrow's events (with a place) in ONE calendar, works out the
driving distance from home, and says whether tonight's charge covers it. Sends at most one card + one message per evening, inside a send window.
Only the place text of an event goes to the free OpenStreetMap geocoder (Nominatim, cached) and coordinates to the OSRM router; event titles never leave the add-on
except in the DM itself. Runs every 10 minutes; sends once per day.
Every house value comes from settings (JSON in plugins[].settings):
  user_id, token_option      Discord user id to DM; add-on option holding the bot token (default discord_token)
  calendar_entity            the ONE calendar entity to read
  home_zone                  zone entity whose latitude/longitude is home (default zone.home)
  soc_entity, range_entity, charge_state_entity, plug_entity, target_entity     car sensors (battery %, range, charging state, plug lock state, charge target %)
  ttf_entity                 time-to-full sensor (minutes) for the card
  status_entity              optional text status sensor for the card (falls back to charge_state_entity text)
  plug_value                 plug_entity state meaning cable locked in (default CHARGING_CABLE_LOCKED)
  picture_url                optional URL of a car picture for the card thumbnail
  recipient_name, car_name, signoff   words used in messages ("Evening <recipient_name>!", "The <car_name>'s on ...", "- <signoff>", card title "<recipient_name>'s <car_name>")
  send_at "19:30", latest "21:30"   send window (HH:MM)
  min_miles 5, margin 10, factor 1.2, low_soc 25, mi_per_pct 1.4
  skip_regex                 locations matching this are ignored (online meetings, 'home', links ...)
  user_agent                 sent to the geocoder (it asks for one that identifies the app)
  ignore_gates               true = ignore the send window and the 'already sent today' check (manual dry run to see the text now)
State: 'state' = {"sent": {day: "sent"|"none"}, "events": {event key: day}}; 'cache' = geocode and route cache. Same shape as the old script's json files.
The last charge line on the card is read from the car_log plugin's state (if that plugin has run).
Dry: reads everything, logs the message and card it would send; sends nothing and saves nothing (the cache stays in memory).
```

Seeding state from an older loop: stop the old loop, then copy its state files into `/data/<plugin>/` (`car_log/charges.json`, `car_tyres/tyres.json`, `car_charge/state.json`, `car_charge/cache.json`). Shapes are unchanged.
