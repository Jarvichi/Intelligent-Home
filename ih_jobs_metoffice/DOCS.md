# Intelligent Home - Jobs - Met Office

## What it does

Child of Intelligent Home - Jobs: Met Office forecast sensors. Dry-run by default. Child of `ih_jobs`.

## Requires

- `ih_jobs` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Jobs - Met Office**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `register_key`, `met_office_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `old_loops_disabled` | No | You switched older scripts off. Default: `false`. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8792`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `register_key` | Yes | Shared secret with the parent (secret) |
| `own_timer_min` | No | Run by itself every N minutes (0 = only when the parent or start-up asks). Default: `0`. |
| `met_office_key` | Yes | Met Office DataHub API key (secret) |
| `assistant_token` | No | Secret for `GET /summary` (read-only weather summary on port 8799). Empty = the endpoint is off. Send `Authorization: Bearer <token>`; add `?format=json` for JSON. It unlocks nothing else |
| `timezone` | No | Day boundaries (default `Europe/London`) |
| `zone_entity` | No | Zone whose latitude/longitude is used. Default: `zone.home`. |
| `fallback_weather_entity` | No | Weather entity used when Met Office data is missing or stale. Default: `weather.forecast_home`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.

## Weather summary for an assistant (since 0.3.0)

`GET http://<home-assistant>:8799/summary` returns plain text (or JSON with `?format=json`): now (condition, temperature), today high/low, rain chance and amount, the next 2 days, and the source used (Met Office, or met.no when the Met Office data is over 3 hours old). It needs `Authorization: Bearer <assistant_token>`; a missing or wrong token gets 401, an empty option turns the endpoint off (404). The token is compared in constant time and unlocks only this path. The output has fixed fields only (no keys, addresses or place names) and the call changes nothing.

## Privacy

The forecast request to the Met Office includes the location of your home zone and your own API key. Nothing else leaves your network.

## Data storage (database since 0.2.0)

The add-on keeps its data in one SQLite file, `/data/ih.db` (shared helper `ihlib/db.py`, WAL mode). Every file under `/data` was reviewed:

| File under `/data` | Where it lives now | Why |
|---|---|---|
| `metoffice_cache.json` (the last Met Office answer: for each weather value the hours, the percentiles and the numbers) | Database: tables `forecast_fetch` (one row per fetch, primary key = fetch time) and `forecast_series` (one row per weather value per fetch, primary key = fetch time + name) | Time series. The fetch time is the primary key, so "newest" and "older than 3 days" use its index. The last 3 days of answers are kept (a few MB at most); older rows are deleted with their series rows. The newest answer is always kept, even if old |
| `options.json` | Stays a file | Supervisor owns it; holds the secrets (`met_office_key`, `register_key`). Secrets are never stored in the database or its snapshots |
| `STOP`, `STOP_LIVE` | Stay files | Kill switches: you create or delete them by hand |
| Add-on log (container log) | Stays text | Logs stay text |
| `trash/` | New folder | The old cache file, deleted after 30 days |
| `backup/` | New folder | Nightly snapshots of `ih.db`, newest 3 |
| `degraded/` | New folder, only used when the database cannot be used | The cache as a plain file, imported again after a restore |

The sensors are never held back by the database: if the database is damaged, too new or fails during a call, the forecast cache goes to a plain file and the job carries on (and it can always fetch the forecast again).

**Start-up:** the add-on opens `ih.db`, applies numbered migrations (a snapshot is taken first when the file already has data), copies the old cache file into the database, reads it back and compares it inside the same transaction, and only then moves the file to `/data/trash`. It purges the trash at start and once a day. From 04:00 it takes one snapshot a night into `/data/backup`, opens the copy read-only, runs the integrity check and logs the row counts (counts only, no forecast values).

**Status:** `/status` (and `/api/status` on the ingress page) shows `db`: schema version, rows per table, last snapshot time, snapshots kept, last restore check, trash files and bytes, import errors, and `degraded`.

**If the database is damaged or too new:** status shows `db_degraded: true`, a `WARNING` and the error, and the log says `DATABASE DEGRADED`. The cache goes to `/data/degraded/`. The damaged file is moved aside as `ih.db.corrupt-<time>`; nothing is recreated.

**Restore** (add-on stopped): copy the newest file in `/data/backup` to `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on. The file in `/data/degraded/` is then imported automatically. To go back to the previous add-on version: install it and copy `/data/trash/metoffice_cache.json` back to `/data/metoffice_cache.json` (the old version re-fetches anyway if it is missing).

Untested on the real device: power loss, a restore from a Home Assistant backup, running two copies at once.

## More details

### How it joins the parent
Give this add-on and the parent the **same** `register_key` (any long random text). Every 30 seconds the child posts a signed registration to the parent (`parent_url`). The parent lists it (`GET /children`) and forwards scheduled runs to it. The child keeps working if the parent is stopped (it runs once 90 s after start, and every `own_timer_min` minutes if above 0). With no `register_key` the link is off. The API is never called more than once per 55 minutes (shared cache in `/data`).

### Going live
Acts only when `mode` is `live`, `old_loops_disabled` is on and `/data/STOP_LIVE` does not exist.
