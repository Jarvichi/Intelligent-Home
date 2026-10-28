# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.8.2] - 2026-10-28

### Changed
- `brain_webhook_id` is optional (legacy; the brain inbox feature moved to its own add-on).

## [0.8.1] - 2026-10-28

### Changed
- Documentation, icon and changelog added.

## [0.8.0] - 2026-10-27

### Added
- Channel administration for modules (create, move, re-topic, delete), all off by default with per-module limits and an audit log.

## [0.7.0] - 2026-10-26

### Changed
- The transport has no commands of its own; feature add-ons answer through signed MQTT messages. Router and bus are always on.

### Removed
- Built-in shopping, house and inbox features (now separate add-ons).

## [0.6.2] - 2026-10-25

### Added
- Option retired_features to free a channel for a live module.

## [0.6.1] - 2026-10-25

### Added
- Shadow mode for modules.

## [0.6.0] - 2026-10-25

### Added
- Signed bus client, router, journal and persisted cursor. All off by default.

## [0.5.0] - 2026-10-25

### Changed
- Home-specific details moved into options.

## [0.4.1] - 2026-10-25

### Changed
- Channel options may be numbers.

## [0.4.0] - 2026-10-25

### Added
- Per-feature live mode and a kill switch.

## [0.3.1] - 2026-10-25

### Changed
- Scheduler messages are signed.

## [0.3.0] - 2026-10-25

### Added
- Self-check scheduled job over MQTT (off by default).

## [0.2.0] - 2026-10-25

### Added
- Optional note-inbox plugin (dry-run).

## [0.1.0] - 2026-10-25

### Added
- Initial release: Discord commands, dry-run only.
