# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.6.6] - 2026-11-03

### Added
- Move the existing player and research dashboard behavior into the Games ingress UI, including per-player library summaries and the open Games research queue.
- Let the previous `/house-games/<player>` dashboard open the matching player page in the Games add-on.

## [0.6.5] - 2026-11-03

### Added
- Request official box art through a Games-only, capability-limited IH AI Layer token.
- Validate official store/publisher source pages, download images securely, and persist them under `/data/artwork` for local UI display.
- Add a per-game “Find official box art” action; it uses the configured SerpApi search quota only when requested.

## [0.6.4] - 2026-11-03

### Added
- Player-by-player ingress game hub backed by the existing private API; API credentials remain server-side.
- Complete Destiny milestone and character details, plus full Arc Raiders quests, needs, maps and inventory advice.
- Read-only game details endpoint for the ingress UI.

## [0.6.3] - 2026-11-03

### Added
- metadata-only trace spans (shared ihlib.trace); off without an ingest token. Includes the pending to-do sync and docs edits.

## [0.6.2] - 2026-10-31

### Fixed
- To-do sync: an open to-do only blocks a new "Games: research ... for <title>" item when its summary is that exact item. Before, any item whose description mentioned the title blocked it and the game was marked done, so Cyberpunk 2077 never got its to-do. Games affected earlier can be re-queued by clearing their `todo` flag.

## [0.6.1] - 2026-10-31

### Added
- Home Assistant sensors (REST state publish, after each providers run): `sensor.games_library_<player>` (state = number of games, attribute `games`) and `sensor.games_<tool>_<player>` (summary string plus reset times, milestones, characters for Destiny; needs list for Arc Raiders). Dry mode only logs. Tools may define an optional `sensor(tc, player)`.

## [0.6.0] - 2026-10-31

### Fixed
- Destiny weekly reset is Tuesday 17:00 UTC (not UK local) in the provider's `reset` event too; shown in UK time (18:00 in BST). Constant is documented as unconfirmed.

### Added
- Per-player game library (`/data/library.json`) built from the provider feeds, with manual add and hide.
- HTTP API (bearer `admin_token`): `/library`, `/library/add|hide|sync-todos|new-games-without-todo`, `/games/<id>/summary|upcoming|cmd`.
- Played summary per game from the feeds.
- Pluggable game tools in `app/tools/` (auto-discovered, `_template.py` scaffold). Tools: `destiny` (local reset times, characters, power, milestones) and `arc_raiders` (need, maps, inv, state edited by command).
- A new game without a tool or to-do creates a "research a tracker" to-do on `todo_entity` (live mode only, never duplicates).
- Options `admin_token`, `arc_data_dir`, `destiny_reset_utc_hour`, `todo_entity`, `todo_sync`.

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

## 0.6.2
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
