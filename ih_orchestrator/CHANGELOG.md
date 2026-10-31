# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.9.1] - 2026-10-31

### Added
- Add-on manager: a "Prebuilt image" line on every add-on card (Add-ons and Install tabs): "yes (tag X)" when the slug is listed in the export's `tools/prebuilt.txt`, otherwise "no (built on the Pi)". The catalogue generator writes `image: true/false` per entry, so no new permissions or Supervisor calls are used.

## [0.9.0] - 2026-10-29

### Added
- Shared facts store. Option `fact_clients` (`{module, key, write}` per module; empty = off, `/facts` answers 404). `PUT/GET/DELETE /facts/<module>/<name>` and `GET /facts?module=<m>`, bearer key per module, a module writes only its own facts, values up to 4 KiB, 500 facts, optional expiry (`ttl_s` or `expires_at`). Saved in `/data/facts.json`. The status page lists fact names and times, never values. Values are never logged.

## [0.8.1] - 2026-10-28

### Removed
- The 9 single-provider AI agent add-ons are gone from the catalogue (Install tab). Their work is now built into the AI Layer.

## [0.8.0] - 2026-10-28

### Added
- Usage: the page now shows which add-ons are really being used. Every 5 minutes the background refresher counts the invocation lines in each add-on's recent log (today, last 24 hours, failed, time of the last one). Add-on cards show "Used: N today (M failed), last HH:MM".
- New Usage tab: add-ons sorted by use over the last 7 days, a small bar per day, and "not used in 7 days" highlighted.
- Daily counts are saved in `/data/usage.json` so they survive log rotation and restarts; the same log line is never counted twice. Only counts and times are stored or shown, never log text. Counts start when this version first runs, and logs hold only a limited tail.

## [0.7.3] - 2026-10-28

### Fixed
- Health: a module that stopped pushing drops out of the list after 6 hours instead of showing FAIL forever.
- Health: new check type `http_host` for URLs only the host can reach (127.0.0.1); the host probe runs it.

## [0.7.2] - 2026-10-28

### Fixed
- "Open config" pointed at the old add-on page address (404 since the apps rename); it now opens the app configuration inside Home Assistant without a page load.
- Buttons crashed the Home Assistant phone app: no browser confirm dialogs (tap twice instead) and tabs switch without links.

## [0.7.1] - 2026-10-28

### Fixed
- Settings tab: options the add-on marks optional are listed as "Optional, not set" and no longer count as missing.
- Plugins tab: schedule and times are readable; Health tab: dropped the empty version column.

## [0.7.0] - 2026-10-28

### Added
- The sidebar page now has tabs: Add-ons, Install, Plugins, Settings, Health. It is the single Intelligent Home page.
- Add-ons are cards that work on a phone: Start/Stop, Restart, Update, inline Logs and a link to the add-on configuration.
- Plugins tab: lists the plugins of the jobs add-on with dry/live, schedule, next and last run and last error; "Make live" / "Make dry" changes the option and restarts the jobs add-on (asks first). Plugin settings are never shown, only their key names.
- Settings tab: names of missing options per add-on and a "Fill now" button for the settings hub.
- Health tab: the services table (filter All/FAIL/OK) from the pushed status.

### Changed
- Installable add-ons moved from the add-on list to the Install tab.

## [0.6.2] - 2026-10-28

### Changed
- Documentation, icon and changelog added.

## [0.6.0] - 2026-10-27

### Added
- Add-on manager panel in the sidebar: install state and version for every Intelligent Home add-on.

## [0.5.0] - 2026-10-26

### Added
- Settings hub: fills empty shared options of the other add-ons from one list.

## [0.4.2] - 2026-10-25

### Changed
- Home-specific details moved into options so the add-on is generic.

## [0.4.1] - 2026-10-25

### Changed
- Scheduler messages are signed with a key per module.

## [0.4.0] - 2026-10-25

### Added
- Optional MQTT scheduler (off by default).

## [0.3.3] - 2026-10-25

### Changed
- The add-on health check type is host-only.

## [0.3.2] - 2026-10-25

### Added
- Each service on the status page shows a description.

## [0.3.0] - 2026-10-25

### Changed
- Modules push their status with a token-protected POST; no folder mapping needed.

## [0.2.0] - 2026-10-25

### Added
- Minimal secrets broker (parked, off by default).

## [0.1.0] - 2026-10-25

### Added
- Status page for the other modules.
