# Polestar

## What it does
Every `interval_min` minutes it reads the Polestar 2 (battery %, range, charging state, odometer) and stores each reading in `/data/ih.db` (kept `keep_days` days). In `live` and `fixture` mode it publishes plain sensors: `sensor.polestar_battery` (%), `sensor.polestar_range` (mi, km in an attribute), `sensor.polestar_charging` (charging / idle / full / scheduled / error / unknown), `sensor.polestar_odometer` (mi), `sensor.polestar_last_update`.

## Modes
- `dry` (default): reads if a login is set, stores, logs, publishes nothing.
- `fixture`: fake moving values, no login needed, sensors carry `fixture: true`. For testing the Cars tab before the car exists. Switch off before real use (the fake sensors vanish on a Home Assistant restart).
- `live`: real values from pypolestar, published.

## Read-only, always
No lock, unlock, climate, charge or wake command exists in the code (a test checks this). Location is not read or published.

## Options
`mode`, `api_token` (bearer for `/status` and `/readings`; `/healthz` is open), `polestar_email`, `polestar_password` (the Polestar ID; never logged, never stored in the database), `vin` (empty = first car on the account), `interval_min`, `keep_days`.

## After the car arrives (Andrew)
1. Put the Polestar ID email and password in the options (the lessor may need to link a leased car to the account first: unconfirmed).
2. Set `mode: live`, read the log. The pypolestar payload shape is UNCONFIRMED: capture one real reply, add it to `tests/test_polestar.py`, adjust `parse_payload` if needed.
3. Set `api_token`; turn on auto_update in the Supervisor page.

## Ingress page
Admin only: latest reading, filter box and Clear, readings table (hides odometer and source on a phone).

## Rollback
Stop and uninstall. Delete the five sensors if wanted (plain states).

## Restore
Stop the add-on. Copy the newest file from /data/backup over /data/ih.db, delete ih.db-wal and ih.db-shm, start the add-on.
