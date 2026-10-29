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
| `files_token` | No | Setting `files_token`. |
| `bods_api_key` | No | Optional Bus Open Data API key for the bus_watch plugin (the plugin setting bods_key_option names this option). |
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
Drop a file in `app/plugins/` exposing `NAME`, `SCHEDULE` (`{"kind":"daily","at":"19:30"}`, `{"kind":"every","minutes":30}` or `{"kind":"service","seconds":90}`), `DEFAULTS`, `run(ctx)`. Turn it on with the option `plugins`: a list of `{name, live, settings}`; `settings` is a JSON text with that plugin's house values. Dry by default (`live: false`: logs what it would do). `/data/STOP_LIVE` or `/data/STOP_<name>` forces dry. State lives in `/data/<name>/`. `/status` shows mode, last run, last error and next run per plugin. Shared helpers: `app/ihlib` (a copy: edit `ihlib/` and run `sync_ihlib.sh`).

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

##### Heating plugins (0.6.0)

`trv_watch` (every 30 minutes). Only ONE live copy may exist (the old host loop or this plugin): keep the plugin dry until the host loop is stopped. Dry logs `would reconfigure <entity>` and sends nothing.

```
trv_watch: every 30 minutes, find radiator valves (ZHA) whose temperature reading has frozen, try a ZHA reconfigure (at most one per valve per 12 h),
and put ONE display message on the display if a valve is still frozen an hour after the reconfigure (at most one per valve per day).
Stage 1 (history): current_temperature has not CHANGED value for `frozen_h` hours and the valve was not 'off' during that time.
Stage 2 (the valve itself): read the valve's own temperature (a radio read, which also refreshes HA's cached value). Frozen when it differs from what HA shows by
`diff_c` or more. If the valve does not answer, act only when it was flat >= 24 h and heating (or off its target) during that time.
Valves are found at run time from the device and entity registries (platform zha, climate, device model == `model`), so no addresses are stored.
SAFETY: the reconfigure is a real action on a heating device. Only ONE live copy may exist: if the old host loop (tmux 'trvwatch') is live, keep this plugin dry
(live false). Never both: they would each keep their own 12 h cap.
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  model             device model text to watch (TRVZB)
  display_script     script entity that shows a display row; called with text 'title | detail' (empty = no display message)
  frozen_h          hours without a change = candidate (6)
  diff_c            stage-2 difference counted as frozen (0.5)
  reconf_gap_h      hours before reconfigure is tried again (12)
  check_after_min   minutes after a reconfigure before the follow-up read (60)
  msg_gap_h         hours between display messages for one valve (24)
  ok_read_gap_h     hours before a valve whose read matched HA is asked again (3)
  history_h         hours of history read per valve (30; more than 24 so the 24 h rule still works; the old script read 72)
  title_strip       text removed from the device name in the message (' Thermostat')
  msg_title         message title, {name} = short device name ('{name} valve stuck')
  msg_detail        message detail ('reconfigure did not help, check batteries')
  dry_device_read   true = a DRY run may also do the stage-2 radio read (read only, costs valve battery); default false
  minutes           run every N minutes (30)
State (plugin state 'valves'): {entity_id: {"reconf_ts", "msg_ts", "ok_read_ts"}}, unix seconds. Same shape as the old script's json, so it can be copied in.
Dry: reads history and states, logs the verdict per valve and 'would reconfigure <entity>' / 'would send display'; sends nothing, saves nothing.
```

`fire_advisor` (every 10 minutes; advise and review are gated by time inside the run):

```
fire_advisor: log-burner helper. Runs every 10 minutes and does three jobs, gated by time inside the run:
  detect (every run)        spot a fire being lit from a quick rise of a room temperature, record it, keep a 'fire is lit' switch in step (on while lit, off
                            when the room cools or after max_lit_hours), send ONE 'stuffy' display nudge per fire when the air-quality value stays high, and (while a fire
                            is lit) write a temperature sample into the watch log every run.
  advise (daily, advise_at) decide whether tonight is a good fire night (cold or windy evening, someone home or expected, heating would run anyway) and put a display row.
  review (weekly)           write a markdown week review (fire evenings vs others, advice followed or not, suggested threshold changes) into plugin state and the log. Nothing is sent.
The old script read the recorder database; this one uses Home Assistant history (a few hours for detect, 4 weeks of presence switches once a day for advise,
7-15 days of a few entities for advise and review) and the statistics websocket call for gas use. Starting the old fire_watch.py script is replaced by the watch log.
Every house value comes from settings (JSON in plugins[].settings). A section you give REPLACES the same-named default section's keys one by one (missing keys keep the default).
  weather_entity      weather entity (outside temperature, hourly forecast)
  people              presence switches ('on' = at home); names in the message come from the entity id with '_home' and the domain removed
  boiler_entity       boiler heating binary sensor (review only)
  ufh_entities        list of underfloor climate entities (review only; hvac_action 'heating' counts)
  gas_entity          cumulative gas kWh statistic id (advise and review; optional: without it the saving estimate is skipped)
  display_script       script entity that shows a display row; called with text 'title | detail'
  room_label          words for the room in the advice title ('fire room')
  watch_entities      {label: entity id} sampled into the watch log while a fire is lit (optional)
  rooms               {name: {detect_enabled, state_enabled, temp (entity), temp_attr (or null), rise_c, window_min, min_temp, flag (input_boolean), purity (entity or null),
                              nudge ('title | detail'), warmed_by [names of rooms whose fire also warms this one]}}
  detect              {cooldown_min 240, watch_minutes 240}
  state               {end_drop_c 0.5, end_window_min 30, end_below_c 25.0, min_age_min 40, max_lit_hours 8, purity_over 1200, purity_min 15}
  advise              {cold_evening_c 10, cool_evening_c 13, windy_kmh 30, min_people_home 1, typical_home_fraction 0.5, earliest_light '15:30', latest_light '19:00',
                       lead_min_before_arrival 60, gas_p_per_kwh 7, fire_displaces_fraction 0.3, evening_share_of_daily_gas 0.4, log_cost_per_fire_gbp null,
                       cost_mode 'cosy_not_wasteful'}   ('strict' = the saving must beat the log cost)
  review              {followed_window_min 60, days 7}
  advise_at '14:00'; review_at '18:30'; review_isoweekday 7 (Sunday); gate_min 30 (a timed job still runs up to this many minutes late)
  minutes             run every N minutes (10)
State: 'fires' (list, same shape as the old fires.json), 'advice' (list, same as advice.json), 'done' (dates of the last advise and review), 'reviews' (last 12 texts),
'watch' (samples per fire). The old json files can be copied in.
Dry: reads history, states and the forecast; logs every decision ('would call ...', 'would record fire ...'); sets no switch, sends no display row, saves nothing.
A timed job (advise, review) is logged once per day in dry. SAFETY: only ONE live copy: the old host loop and this plugin would both set the fire switch and send rows.
```

Both need Home Assistant history (REST) and the websocket; the add-on has no access to the recorder database, so history windows are bounded (see each plugin's settings).

##### Bus plugin and the `service` schedule kind (0.7.0)

Schedule kind `service`: the host starts one supervised thread per configured service plugin and calls `run(ctx)` every `seconds` (the plugin default, or `"seconds"` in its settings). Every pass gets a fresh context, so `live`, the kill-switch files (`/data/STOP_LIVE`, `/data/STOP_<name>`) and the clock are re-read each time; an exception in a pass is logged and the loop continues. The 30-second timer never runs a service plugin. Why not `every`: `every` is whole minutes and runs on the shared timer thread, so a slow network call would delay every other plugin; a service pass has its own thread and a seconds interval (90 s for the bus feed). The plugin keeps its progress in state between passes instead of looping inside one call, so a restart in the middle of a window loses nothing.

`bus_watch` (service, 90 s). A recorder only: it has no DM mode. Dry reads the feed (read only) and the vehicle page and logs `would log shadow row`, `would write promise record` and `would POST`; it writes nothing to disk and posts nothing. Live writes `shadow.jsonl`, `promise_log.jsonl`, `sessions.json` and `vehicle_types.json` under `/data/bus_watch/`, and POSTs the promise log to the webhook in settings.

```
bus_watch: a recorder for one bus route. Polls the Bus Open Data (BODS SIRI-VM) vehicle feed during morning windows, projects each bus onto the route to estimate
minutes to a stop, keeps a SHADOW log (every poll, for scoring the estimate later) and a PROMISE log (one record per day and stop: which vehicle came and whether it
was a double-decker). It never sends a DM and has no 'run' mode: it is a recorder only. Runs as a `service` plugin (its own thread, one pass every `seconds`, 90 by default).
A pass outside every window returns at once without touching the network.
Day logic (same as the old host script): on a day with rules for today that is not in `closed` and is Monday-Friday, each rule {stop, time} is tracked from
(time - start_before_min) to (time + shadow_after_min): the bus whose estimated arrival is nearest `time` is followed, a 'passed' row ends it, and ONE promise record is written.
Any other day (weekend, closed day, weekday without rules) is a SWEEP from sweep_start to sweep_end: every inbound bus seen within sweep_km of `sweep_stop` is recorded
(an empty window is 'no_info', never a no-show, because no timetable is known).
The estimate is distance along the route x road_factor / avg_speed_kmh; it is NOT a timetable estimate.
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  operator_ref, line_ref   feed filters (required)
  feed_url                 BODS data feed URL (the public one)
  bods_key_option          NAME of the add-on option holding the BODS API key (bods_api_key). The key is never logged.
  presence_entity          reserved for a future DM mode; not used (this plugin never sends)
  direction                DirectionRef to follow (inbound)
  stops                    {key: {name, code, lat, lon}}  the stops that rules refer to
  schedule                 {mon..sun: [{stop: key, time: 'HH:MM'}]}  time = timetabled time at THAT stop
  closed                   [[from 'YYYY-MM-DD', to, reason]]  days with no service (anything unsure counts as closed)
  route_stops              [{code, lat, lon, name}] real stop coordinates of the route in order, ending at or after your stops (best), OR leave empty and
                           put the old route_stops.json in plugin state; with neither, a rough 'memory' route is built from route_polyline
  route_polyline           [[lat, lon], ...] rough route (first 2 points, then the stop, then the last point) and the closing point used with route_stops
  start_before_min [25], shadow_after_min [25], dm_when_eta_min [15] (only marks 'would_dm' in the log), avg_speed_kmh [35], road_factor [1.25], stale_min [5]
  sweep_start ['07:00'], sweep_end ['09:00'], sweep_km [1.5], sweep_stop (key), promise_stop (key of the promised journey; default sweep_stop)
  vehicle_page_url        page per vehicle with an og:description type text; {veh} = vehicle ref lower case (bustimes style); looked up 1 per second, cached
  promise_webhook_url     host URL that receives {"rows": [all promise records]} after every change (page rebuild stays on the host). Empty = not posted.
  keep_days [90]          shadow log rows older than this are dropped once a day
  seconds [90]            pass interval
Config seeding: lists/dicts you leave empty are read from plugin state (the folder /data/bus_watch/): config.json = the old config.json (stops, schedule, closed,
route_polyline), route_stops.json = the old route_stops.json, vehicle_types.json = the old type cache. Shapes are unchanged, so the old files can be copied in.
Files in /data/bus_watch/: shadow.jsonl (same rows as the old shadow.jsonl), promise_log.jsonl (same records as the old promise_log.jsonl), sessions.json, vehicle_types.json.
```

Page rebuild: the page generator stays on the host. After every change to the promise log (live only) the plugin POSTs `{"source": "bus_watch", "rows": [...]}` to `promise_webhook_url`. A failed post is retried on the next pass. The host side must (1) write the rows to its `promise_log.jsonl` and (2) run its page builder; that receiver is not part of this add-on. The same rows are also on disk in `/data/bus_watch/promise_log.jsonl`.

SAFETY: the old host loop (tmux `robbus`) and this plugin both append records. Keep the plugin dry until the host loop is stopped, then copy the old `promise_log.jsonl` (to `/data/bus_watch/`) and `vehicle_types.json` first. Never both live. The BODS key goes in the add-on option `bods_api_key`.

##### Photo plugins and folder access (0.8.0)

New in 0.8.0: `config.yaml` has `map: [media:rw, share:rw]` (approved for the photo plugins; no other plugin uses the folders), and the image has Pillow 11.0.0 (pinned in the Dockerfile; binary wheels for Python 3.12 on aarch64 and amd64). If Pillow is missing, a live photo plugin logs "Pillow is not installed ... (rebuild the add-on)" and does nothing; dry runs still work.

New helper `ctx.ha.subscribe_events(event_type, on_event, max_seconds, on_idle)` (ihlib): one websocket subscription for one event type; returns after `max_seconds` so a service pass is bounded. `WS.wait(timeout)` makes sure a read is never cut half way.

**Who writes what.** `photo_catalogue` owns `index.json` and the automatic album folders (by-topic, by-month, `_thumbs/by-*`, `_family/by-*`). `photo_albums` owns `albums.json` (the registry), `_lib`, `_originals`, `_uploads`, `_failed`, `_state`, `my-albums` (also in `_family` and in the file share). Each only READS the other plugin's file (`ctx.sibling_state`). Both run in the same process and take one shared lock (`_photolib.LOCK`) around every write into the media and share folders, so a nightly catalogue and an album click never overlap. Files `index.json` and `albums.json` are the plugin state files `/data/photo_catalogue/index.json` and `/data/photo_albums/albums.json` (same JSON shape as the old files; written compact instead of indented).

`photo_catalogue` (daily, 03:20):

```
photo_catalogue: nightly catalogue of photos into viewable albums.
Sources: <brain_dir>/attachments/** and every folder in `media_sources` (an allow-list: nothing else is scanned).
Outputs: index.json (state), <media_out>/by-topic/<topic>/<name>.jpg (1600 px), by-month hard links, _thumbs/* (400 px), _family/* (only auto albums picked in the
registry), <share_out>/{by-topic,by-month} real copies (skipped when share_out is empty). Captions in index.json are kept on re-run.
Privacy rules are SETTINGS: skip_name_regex (file names never catalogued), private_note_marker (a photo whose first mentioning note has this text in its path is skipped).
  at ['03:20']  brain_dir ['/share/brain']  media_out ['/media/albums']  share_out ['']  media_sources []
  skip_name_regex [generic list: licence/passport/receipt/insurance/bank/card/statement/id/private]  private_note_marker ['private']
  big_px [1600] thumb_px [400] jpeg_quality [82]  share_uid [1000] share_gid [1000]
  auto_family_seed []   auto albums picked for the family until the registry exists
  seed_index ['']       path of an old index.json read once when the plugin has no index (keeps captions); never written
Dry: scans and logs counts and what it would resize, link and copy; writes nothing.
```

`photo_albums` (service):

```
photo_albums: manual albums for a dashboard card. Each pass subscribes to the Home Assistant event `event_type` for `listen_s` seconds and handles every event; once a minute it
ingests files dropped in <media_out>/_uploads and refreshes the sensor. Actions: refresh, create_album, rename_album, delete_album, add, remove, move, set_family, delete_photos,
ingest. Destructive and sharing actions need confirm:true.
  event_type ['house_photo_action']  sensor_entity ['sensor.photo_albums']  media_out ['/media/albums']  share_out ['']  skip_name_regex [generic]
  big_px [1600] thumb_px [400] jpeg_quality [82]  share_uid [1000] share_gid [1000]
  seconds [2] listen_s [600] periodic_s [60] settle_s [20] max_upload_mb [10]
  auto_family_seed []  seed_registry ['']   path of an old albums.json read once when the plugin has no registry (never written)
Dry: subscribes (read only), runs each action on an in-memory copy and logs the result; no file, sensor, registry or upload is touched.
```

Both plugins must use the same `media_out`, `share_out` and `skip_name_regex` values.

**Known limits.** A pass ends every `listen_s` seconds and the next starts `seconds` later, so an event fired in that gap (a few seconds every ten minutes) is lost; the card shows the result from the sensor, so a missed click is visible and can be repeated. An action runs on the plugin thread: a long nightly catalogue holds the lock, so clicks wait until it ends.

**Pillow versus the old node resizer.** Same: file names, folder layout, `index.json` and `albums.json` fields, the 1600 px / 400 px sizes, JPEG quality 82, existing files are skipped, never enlarged, EXIF data not copied. Different: (1) resampling is Lanczos (the old tool used its default bilinear), so pixels differ slightly and the files are not byte-identical; (2) the EXIF orientation is applied (phone pictures taken sideways come out upright); (3) a picture with transparency (PNG) is flattened onto white; (4) output is written to a temporary file and renamed, so an interrupted run leaves no half file; (5) the target size follows `round(w x scale)` (checked within 1 pixel against the old tool in tests/test_photo_compare.py).

**Taking over from the host loops (overseer steps).** Keep the plugins dry until the tmux sessions `photocat` and `photoalbums` are stopped (two live copies would both write the same folders). Then: (1) stop both sessions; (2) put copies of the old `index.json` and `albums.json` where the add-on can read them (for example a temporary folder in `/share`) and set `seed_index` / `seed_registry` to those paths; (3) set both plugins live; the first live run saves the seeded files as plugin state, after which the seed settings are ignored; (4) remove the seed copies. Settings values for the real house live on the host (the photo scripts and the old tmux lines), not in this repository.

**Plugin files.** Set the option `files_token` to let the host read and seed plugin state under `/data/<plugin>/`. Empty means off (403). Send `Authorization: Bearer <files_token>` (wrong token: 401). `GET /plugin/<name>/files` lists `{"files": {name: bytes}}`; `GET /plugin/<name>/file/<file>` returns the raw file (404 if missing); `POST /plugin/<name>/import` with `{"files": {name: text}, "overwrite": false}` writes files atomically (mode 0600) and answers `{"ok": true, "written": [...], "skipped": [...]}`. Only plugin names that exist and file names matching `[A-Za-z0-9_][A-Za-z0-9_.-]*.json` or `.jsonl` are accepted. Every file is checked first (valid JSON, every `.jsonl` line valid JSON); one bad file rejects the whole request (400) and nothing is written. Body limit 1 MiB (413). Without `overwrite`, existing files are skipped.
