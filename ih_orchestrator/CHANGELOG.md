# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
