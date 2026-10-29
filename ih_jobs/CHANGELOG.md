# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.11.0] - 2026-10-29
### Added
- Plugin `brain_inbox` (service, polls every 10 seconds): files messages from a Discord channel (default `brain-inbox`) sent by allowed users into the brain inbox as notes, with attachments, and reacts with a check mark. Uses the existing `discord_token` option and the existing share folder map. Dry runs only read the channel and log note names and sizes.

## [0.10.1] - 2026-10-29
### Added
- `POST /plugin/<name>/run` (files_token): run a timer plugin now, for example a persona's "check email". Service plugins answer 409.

## [0.10.0] - 2026-10-29
### Added
- Plugin `mail_inbox` (every 30 minutes): files unread mail from allowed senders that pass the DMARC check into the brain inbox (notes and attachments), marks everything else read, and logs counts only. Dry runs connect read-only and change nothing.
- Option `mail_password` (password, optional): the mailbox app password for `mail_inbox`.

## [0.9.1] - 2026-10-29
### Fixed
- bus_watch could never read the bus feed: the `bods_api_key` option was not passed to plugins.

## [0.9.0] - 2026-10-29

### Added
- Option `files_token` (empty = off, endpoints answer 403). With it, `GET /plugin/<name>/files`, `GET /plugin/<name>/file/<file>` and `POST /plugin/<name>/import` let the host read and seed plugin state files (`.json` / `.jsonl` only, validated, atomic, 1 MiB limit).

## [0.8.2] - 2026-10-28

### Fixed
- Event listeners (photo_albums) no longer drop and reconnect every 50 seconds: a websocket ping from Home Assistant is answered and the listener goes back to waiting.

## [0.8.1] - 2026-10-28

### Changed

- Uses the shared ihlib library for Home Assistant calls and log masking. No change in behaviour.

## [0.8.0] - 2026-10-28

### Added
- Folder access: `map: media:rw, share:rw` in `config.yaml`. Needed for the two photo plugins only (the albums live under `/media`, the brain photos and a private file share under `/share`). No other plugin reads or writes these folders. This widens what the add-on can touch, so check it when updating.
- Plugin `photo_catalogue` (daily 03:20): catalogues photos from the brain attachments and allow-listed media folders into viewable albums (1600 px copies, 400 px thumbnails, by topic and by month, optional private file-share copy, family-shared albums). Skip rules (file-name pattern, private-note marker) are settings. Dry by default.
- Plugin `photo_albums` (service): manual albums driven by a dashboard card through a Home Assistant event (create, rename, add, move, share, delete, upload ingest). Dry by default (runs each action in memory only).
- Pillow 11.0.0 in the image (resizing; replaces the old node tool). Missing Pillow is logged and the live plugin does nothing.
- Shared library: `HA.subscribe_events` (bounded event subscription over the websocket) and `WS.wait`.

## [0.7.0] - 2026-10-28

### Added
- New plugin schedule kind `service`: the host runs the plugin in its own supervised thread, one pass every `seconds` (settings can override it). Each pass gets a fresh context, so dry/live, kill-switch files and the clock are re-read; a crash in a pass is logged and the loop carries on. The 30-second timer ignores service plugins.
- Plugin `bus_watch`: recorder for one bus route during morning windows. Reads the Bus Open Data vehicle feed, estimates minutes to a stop, keeps a shadow log and a promise log (which vehicle came, double-decker or not), and can POST the promise log to a host webhook so the host can rebuild a page. Never sends a message. Dry by default (reads the feed, logs what it would write, writes nothing).
- Option `bods_api_key` (optional password) for the feed key; plugins read options through `ctx.option(name)`.
- Shared library: `Http.post_json` (JSON POST, returns the status code).

## [0.6.0] - 2026-10-28

### Added
- Plugin `trv_watch`: every 30 minutes finds radiator valves (ZHA) whose temperature reading is frozen, reconfigures them (12 h cap per valve) and puts one display message if still frozen an hour later. Dry by default; only ONE live copy (host loop or plugin) may run.
- Plugin `fire_advisor`: log-burner helper (fire detection every 10 minutes, fire-lit switch and stuffy nudge, daily advice row, weekly review text in plugin state).
- Shared library: `ihlib.series` (history rows to time series, windows, value-at), used by both plugins. Websocket calls (`ctx.ha.ws`, `ctx.ha.ws_many`) now used for registries, ZHA and statistics.

## [0.5.0] - 2026-10-28

### Added
- Plugin `car_log`: logs electric-car charge sessions with their cost (every 10 minutes); can publish a summary entity for a dashboard.
- Plugin `car_tyres`: DM with a picture when a tyre is low (cold tyres only, with a cooldown).
- Plugin `car_charge`: evening car card and heads-up about tomorrow's calendar trips (geocoder and router lookups, cached).
- Plugin helpers: `ctx.http` (JSON and picture fetch with a polite per-host gap), `ctx.discord.dm(..., embeds=, file=)` (dry = log only), `ctx.ha.calendar_events`, `ctx.sibling_state`.
- Shared library: Discord messages with embeds and one attached file.

## [0.4.2] - 2026-10-28

### Changed
- `mqtt_password` and `signing_key` are optional: only the orchestrator scheduler (off by default) uses them. Status shows the right version.

## [0.4.1] - 2026-10-28

### Added
- Plugin `birthday_reminder`: a daily DM about birthdays 7 and 3 days ahead (list in settings).

## [0.4.0] - 2026-10-28

### Added
- Plugins: extra jobs can be added in app/plugins and enabled by option.

## [0.3.0] - 2026-10-26

### Added
- Parent of the Jobs family: lists and triggers child job add-ons, which register with it.

## [0.2.0] - 2026-10-25

### Changed
- Home-specific details moved into options.

## [0.1.0] - 2026-10-25

### Added
- Initial release: backup storage sensors and Met Office forecast, dry-run by default.
