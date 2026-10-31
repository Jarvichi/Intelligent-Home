# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.10.0] - 2026-10-31

### Added
- Approvals API (port of the host approvals.py, CULL row 17): `POST /approvals/ask|step|check|list|delete|hooked` on the health port, bearer `admin_token`. Posts the approval / manual-step embed with tick, cross and question reactions in the channel configured as `extra_channels` key `approvals`, reads answers back (retries Discord 429), keeps `/data/approvals_open.json`. An answered overnight approval is flagged `run_overnight` once so the host shim can run overnight/approve.py. Waiting is done by the caller polling `check`.
- Option `approvals_live` (default false): dry posts and deletes nothing. `/status` shows `approvals`.

## [0.9.0] - 2026-10-31

### Added
- Bot identity admin (port of the host set_avatar.py, set_nick.py, say.py, gif.py): `POST /identity/avatar|nick|say|gif` on the health port, bearer `admin_token` (off when empty). One set of personas: `house` = the main bot (`discord_token`), more bots from the `personas` option (secret text `name=token,name2=token2`). Avatar: base64 PNG/JPEG/GIF up to 6 MB, 2 changes per 10 minutes per bot. Nick: all servers, empty resets. Say: channel number or a configured channel key. GIF: GIPHY search (PG only, `giphy_api_key`), optional post of the link.
- DRY by default: nothing is sent to Discord unless `identity_live` is on and the kill switch file is absent. Tokens and keys are never returned or logged. `/status` shows `identity` (enabled, live, persona names).

## [0.8.3] - 2026-10-28

### Changed
- Uses the shared ihlib library for Home Assistant REST calls (via core.py). No change in behaviour.

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
