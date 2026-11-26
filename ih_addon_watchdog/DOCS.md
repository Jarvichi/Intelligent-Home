# Add-on Watchdog

**Why:** on 22 Nov 2026 the HA Codex terminal hung and nothing restarted it. This add-on does that job for every add-on.

## What it does
- Every `interval_seconds` it reads every add-on from the Supervisor. A probe is OK when the state is `started` and, if the add-on has an ingress port, it answers an HTTP request within `http_timeout_seconds` (any HTTP status counts as alive).
- A stopped add-on counts as failed only if it starts on boot (`boot: auto`) and the watchdog saw it running before. Stopping one on purpose: switch off its toggle on the page first.
- After `failed_probes` failed probes in a row it restarts the add-on (LIVE mode). Cool-down `cooldown_minutes`; at most `max_restarts_per_hour` per add-on, then it only escalates.
- `never_restart` (default: core, supervisor, this add-on, ih_orchestrator, Mosquitto) gets a task only. Entries match the slug without its repository prefix. Add-ons with a running job must be put on this list by hand: the watchdog cannot see jobs.
- At failure it saves the last 80 log lines (secrets, addresses, MACs and e-mail removed) with the probe result and the action.
- It files ONE high task per add-on (source `ih_addon_watchdog`); repeats become comments. Needs the Tasks add-on token (`tasks_token`).
- Media check (LIVE only, once per 30 min per player): plays a silent clip on each `media_players` entity and expects `playing` within 10 s. If not, it reloads that player's config entry and retries once. Uses `/media/ih_watchdog/silence.wav`, made on first use.
- `/healthz` on port 8851 feeds the Supervisor watchdog.

## DRY and LIVE
Starts in DRY: logs and files tasks marked `[DRY]`, restarts nothing, no media check. The page has a DRY/LIVE switch (it overrides the `mode` option).

## Data
Own database `/data/ih.db` (incidents 30 days, restarts 3 days). No secrets in it. Uninstall removes it.
