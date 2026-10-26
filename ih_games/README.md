
# Intelligent Home Games (`ih_games`)

A Home Assistant add-on that transforms gaming activity into friendly chat notifications.

## What it does
1. **Providers** poll platform APIs (e.g., Home Assistant PSN sensors) and write standardized atomic JSON facts feeds (`/data/feeds/<provider>_<player>.json`).
2. **Orchestrator** reads all facts feeds, applies quiet-hour and rate-limit rules, picks a friendly message template (max 1 a day, 2 if something big like a platinum or level-up, 3 h apart, merged, held in quiet hours), and posts a HA REST event `ih_games_message`.

## Going Live
Set `mode: "live"` in options. By default, `ih_games` runs in `dry` mode (logs actions without emitting external events). Creating `/data/STOP_LIVE` acts as an instant kill-switch forcing dry mode.

## Options
| Option | Type | Default | Description |
|---|---|---|---|
| `mode` | `dry` \| `live` | `dry` | Execution mode |
| `players` | list | `[]` | Configured players list |
| `providers_enabled` | list | `["psn_trophies"]` | Enabled providers: `psn_trophies`, `xbox_gamerscore` |
| player `xbox_prefix` | str | | HA Xbox sensors `sensor.<prefix>_gamerscore`; optional `xbox_gamerscore_entity`, `xbox_now_playing_entity` override the names |
| `quiet_start` | str | `"22:00"` | Quiet hours start (no messages) |
| `quiet_end` | str | `"08:00"` | Quiet hours end |
| `timezone` | str | `Europe/London` | Used for quiet hours and "one a day" |
| `ingest_url`, `ingest_token` | | | Status page add-on |
| `mqtt_*`, `enable_scheduler`, `signing_key` | | | Signed scheduler (same as ih_jobs) |

## How to add a Provider in 5 steps
1. Create `app/providers/<name>.py`.
2. Define `NAME = "<name>"` at module level.
3. Implement `run(ctx, player)` accepting context and a player dict.
4. Compare state using files in `/data/state_<name>_<player>.json` and append new events.
5. Atomically write feed to `/data/feeds/<name>_<player>.json` keeping the last 50 events. Register the provider in `app/main.py`.

## Privacy
No real personal data, tokens, or credentials are logged or hardcoded.



## Sources
`app/schedule_client.py` and `app/jobs/common.py` are COPIES of the ih_jobs files.

## Tests
`python3 tests/test_games.py` (fake data only).
