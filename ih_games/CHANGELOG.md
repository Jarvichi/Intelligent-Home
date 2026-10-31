# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.5.0] - 2026-10-31

### Added
- Destiny provider: per-player option `destiny_reset` (opt in). Tuesday 17:00 UK, once per week, adds a `reset` event.
- Destiny provider: writes a characters and recent-sessions note to `/data/notes/destiny_<player>.md` (also in the feed as `current.note_md`). It does not write into the brain: the add-on has no brain access.

## [0.4.0] - 2026-10-29

### Added
- Options `assistant_url` and `assistant_token`. When `assistant_url` is set, each message is posted to the Assistant add-on as a `game_session` event for the persona named by the player's `speaker`, and not written to the outbox. If the post fails the outbox line is written as before. Empty `assistant_url` keeps the old behaviour. Dry-run logs `[DRY] would send event to persona X`.

## [0.3.4] - 2026-10-28

### Changed
- `mqtt_password` and `signing_key` are optional: only the orchestrator scheduler (off by default) uses them.

## [0.3.3] - 2026-10-28

### Added
- Optional log_payloads option for one-line request logging.

## [0.3.2] - 2026-10-26

### Added
- Feeds summary in /status.

## [0.3.1] - 2026-10-26

### Added
- Generic per-player voice option.

## [0.3.0] - 2026-10-26

### Added
- Destiny and Steam presence providers; per-player overrides.

## [0.2.0] - 2026-10-26

### Added
- Xbox gamerscore provider.

## [0.1.4] - 2026-10-26

### Added
- Fallback timer when the signed scheduler is off.

### Fixed
- Start script shebang.

## [0.1.2] - 2026-10-26

### Changed
- Health port moved to 8794.

## [0.1.1] - 2026-10-26

### Added
- Outbox and ack endpoints for message delivery (dry by default).

## [0.1.0] - 2026-10-26

### Added
- Initial release: PlayStation trophies provider and message orchestrator, dry-run by default.
