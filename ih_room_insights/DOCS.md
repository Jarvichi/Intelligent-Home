# Room Insights

## What it does
Every `interval_min` minutes it reads the last `history_days` days of each TRV (`climate.*`) and each humidity sensor.
1. **Warm-up learning.** A run = consecutive readings with `hvac_action: heating`. Runs of 20 min or more that rose 0.5 C or more give minutes per degree. The median of the last 20 runs per room is kept in `/data/ih.db`. Output: `sensor.ih_warmup_<room>` = minutes to reach the TRV's own target from now (0 = reached). Reference: the office warm-up of 19 Oct (18.3 to 21.5 C in about 80 min, about 25 min per degree).
2. **Frozen valve.** `sensor.ih_frozen_valves` = number of suspect rooms (list in attribute `rooms`). Suspect = reading unchanged for `frozen_hours` (6) while the valve calls for heat or is 1 C or more below its target, or unavailable that long.
3. **Mould risk.** `sensor.ih_mould_risk` = `ok`, `watch` (above `mould_pct` now) or `high` (above for `mould_hours` without a break). Per-sensor detail in attributes. A row only: no push, no sound.

## Safety
No service calls exist in the code. The only writes are the three kinds of sensor above, and only in `mode: live`. In `dry` it computes and logs only. Excluded by default: underfloor, car, garage and the hall Nest.

## Options
`mode` dry|live, `api_token` (bearer for `/status`, `/insights`; `/healthz` is open), `interval_min`, `history_days`, `frozen_hours`, `mould_pct`, `mould_hours`, `rooms` (list of climate entities; empty = auto), `exclude` (words), `humidity_sensors` (empty = every humidity sensor).

## After install (Andrew)
Set `api_token`, turn on auto_update in the Supervisor page of the add-on. Boot is auto and the watchdog is set in config.yaml. Dry first: read the log, then switch to `live`.

## Rollback
Stop and uninstall. Delete the three sensor kinds if you want (they are plain states and vanish on a Home Assistant restart).

## Restore
Stop the add-on. Copy the newest file from /data/backup over /data/ih.db, delete ih.db-wal and ih.db-shm, start the add-on.
