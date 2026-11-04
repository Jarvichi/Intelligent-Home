# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.16.0] - 2026-11-05

### Added
- New plugin `spare_ai`: each night it spends the unused part of each free AI provider's daily allowance (read from the AI Layer `/status`) on queued, non-private tasks and on a free second-opinion review of the last day's commits to a public GitHub repository. Each task is asked of several different providers, then compared by another one. Answers are unverified and saved as Markdown files. Dry by default (no AI calls).
- New add-on options `ai_layer_url` (default `http://172.30.32.1:8801`) and `ai_layer_token` (the AI Layer `api_token`; password).

## [0.15.7] - 2026-11-04

### Added
- Show Jobs child reports inside Manager using their existing signed status channel; retain original sample times so stale children cannot appear freshly reported.

## [0.15.6] - 2026-11-04

### Added
- Push a bounded, read-only Jobs report into Intelligent Home Manager, including mode, built-in runs, errors, child count and last activity; stale reports are labelled.

## [0.15.5] - 2026-11-04

### Fixed
- Remove deployment-specific identity examples from tracing source.

## [0.15.4] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs. Plugin event spans carry the payload size.

## [0.15.3] - 2026-11-04

### Changed
- Any Jobs child add-on can register its own job (name: lowercase letters, digits, `_`), not only `backupstore` and `metoffice`. Child-only jobs are run through their child (no built-in copy). Fixes `ih_jobs_trust` being refused.

## [0.15.2] - 2026-11-04

### Changed
- Add an ingress-only summary of built-in and plugin runs, modes, outcomes, timestamps and unavailable states.

### Fixed
- Remove the house-specific fleet page from the public default; the optional fleet fallback is now disabled until an operator-specific URL is configured privately.

## [0.15.1] - 2026-11-03

### Added
- one metadata-only trace per plugin pass (shared ihlib.trace; service-loop plugins are not traced); off without an ingest token. Also publishes the free_power_value plugin (0.15.0, dry first).

## [0.15.0] - 2026-11-01
### Added
- Plugin `free_power_value` (hourly, dry first): after each supplier free electricity session or Happy Hour, works out the kWh used (Home Assistant long-term statistics), what it would have cost at the normal rate, and the extra over the usual baseline. Keeps one line per session and a winter total (season 1 Nov to 14 Feb) in its own data file; posts one message per session block (live) or logs it (dry).
## [0.14.3] - 2026-11-02

### Added
- Report last successful plugin completion and mode, preserving that evidence after a later failure. Success includes dry runs and expected skips; it is not a delivery acknowledgement.

## [0.14.2] - 2026-11-01
### Fixed
- Count and log plugin failures when run or event handlers return false.

## [0.14.1] - 2026-10-31
### Fixed
- `photo_albums` waits out a Home Assistant restart inside the pass (one "not reachable" line, no error).
- `brain_inbox` treats 429, 5xx and network errors as retry (keeps channel and cursor); refusals log the HTTP code.
- `bus_watch` dry mode keeps in-progress sessions across an add-on restart.
### Added
- `car_tyres` draws its own tyre picture (setting `draw_picture`, default true).
- `car_tyres` setting `tyre_image_url`: use a transparent top-down car PNG (fetched at send time) under the four pressure labels instead of the drawn line-art car. Pure Python PNG decode (all colour types, bit depths 1-16, all filters); any failure falls back to the drawn car.

## [0.14.0] - 2026-10-31
### Added
- Plugin `speed_measure` (every 6 hours): download speed from 4 parallel downloads, published as a sensor (port of the speedtest shell script).
- Plugin `brain_stats` (hourly): brain size and complexity numbers plus search timing, published as a sensor. Reads the brain folder in share, read-only.
### Changed
- `car_log` `publish_entity` now also writes the unit (GBP), icon and name, so it can replace the old command-line sensor.

## [0.13.1] - 2026-10-30
### Changed
- Plugin `receipt_photos` now needs no paid key. New setting `mode` (default `handoff`): receipt photos from the shopping channel are saved to `/share/receipts/inbox/<message id>/<n>.<ext>` and the acknowledgement is posted; the host reads them on the owner's own Claude subscription, ticks the open items and posts the summary. Dry logs `[DRY] would save receipt N photo(s) B bytes` and writes nothing. The paid API mode is kept (`mode` api, only works when the option `anthropic_key` is set). New setting `inbox_dir`.

## [0.13.0] - 2026-10-30
### Added
- Plugin `receipt_photos` (every 5 minutes): receipt photos posted in the shopping channel by allowed users are read by the Anthropic API, matching items on the shopping list are ticked, and a summary is posted (port of receipt_check.sh and receipt_apply.py). Dry runs read the channel and download the photos but make no AI call and change nothing; they log counts only.
- Option `anthropic_key` (password, optional): the Anthropic API key for `receipt_photos` (live only).

## [0.12.0] - 2026-10-30
### Added
- Plugin kind `event` (no timer): `POST /plugin/<name>/event` (files_token, JSON object up to 16 KiB) hands one event to a plugin's `event(ctx, payload)`. Same dry/live and kill-switch rules as other plugins. `POST /plugin/<name>/run` answers 409 for event plugins.
- Plugin `eufy_person` (event): a person at an outdoor Eufy camera -> the event picture and a line of text in a Discord channel (default `camera`), with the host script's rules (5 minutes per camera, fresh-picture checks, duplicate picture check). Uses the existing `discord_token` option, `homeassistant_api` and media map. Dry runs fetch the picture and log `[DRY] would post camera=<key> image=<bytes> bytes` only.

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

## 0.14.4
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
