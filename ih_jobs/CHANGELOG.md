# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
