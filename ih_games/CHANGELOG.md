# Changelog

## [0.13.1] - 2026-11-11

### Fixed
- Named PSN trophies were de-duplicated by the bare trophy id. Trophy ids repeat in every game (0, 1, 2...), so a trophy in one game could be dropped after the same id was recorded in another game. The state file, the history file and the feed events now use the game's PSN communication id together with the trophy id.

### Changed
- Migration: old bare ids in `state_psn_trophy_names_<player>.json` move to `legacy_ids`, and old lines in `trophy_history_<player>.jsonl` stay in the file. Old entries cannot be matched to a game, so they are kept but not used for de-duplication. Trophies earned before the last check are still not announced again.

## [0.13.0] - 2026-11-11

### Added
- Cyberpunk 2077 quest tracker: prerequisites and choice branches. The default list now shows only the quests that are available now. A quest with a prerequisite stays out of that list until the prerequisite is ticked. Two more lists: "Locked" (each quest names the quest it needs) and "Missed this run" (quests that can no longer be done in this playthrough, each with the quest and the choice that ruled it out). Use "Missed this run" to plan a later playthrough.
- Quest data fields in `app/tools/cyberpunk_quests.json`: `requires` (all of these), `requires_any` (one of these), `excludes` (quests ruled out when this quest is done), `choice` and `note` (text from the wiki), and `requires_unknown`. Of the 281 quests, 219 have a prerequisite from the wiki, 21 are first jobs with no prerequisite, and 41 are marked `requires_unknown` because the wiki gives no usable prerequisite. A quest marked `requires_unknown` is always shown as available.
- A missed quest also rules out the quests that need it (for example, the lifepath-only jobs of the two lifepaths not chosen, and the Phantom Liberty jobs behind the path not taken).
- An available quest that would rule out other quests says so before it is ticked. The message after a tick names the quests that are now missed; the message after an untick names the quests that are possible again.
- Chat commands `missed` and `available [group]`. `status` and the sensor attributes also give the available, locked and missed counts.

### Changed
- The progress line shows "done / total" and the missed count. The total stays 281.
- The tool's `details()` rows carry `state` (`done`, `available`, `locked`, `missed`), `reason`, `hint` and `blocks`.

## [0.12.0] - 2026-11-11

### Added
- Cyberpunk 2077 quest tracker (`app/tools/cyberpunk.py`, UI component `cyberpunk`). The console gives no quest data, so it is a manual tick list: open the game on the Games page, tick a quest when it is done, untick it under "Done quests" to undo. Nothing is ticked at the start. Three groups: Base game (226 quests), Phantom Liberty (51) and Patch quests (4), 281 in total, with a name filter.
- The quest list is the static file `app/tools/cyberpunk_quests.json` (id, name, group, type main/side/gig), built on 2026-11-11 from public wiki pages. Sources, the fetch date and what the second source did not confirm are in the file and in DOCS.md.
- The tool reports `progress()`, so the Cyberpunk 2077 card shows "ticked / total" quests in its stat box.
- Chat commands `done <quest>`, `undone <quest>`, `status` and `left [group]`. Ticks are kept per player in `/data/cyberpunk/state_<player>.json`.

## [0.11.0] - 2026-11-11

### Changed
- Game card: "Last seen" is now "Last played".
- Game card: the "Play time" box is replaced by one stat box. It shows, in this order: quest progress ("done / total") when the game's tool reports it; else trophies ("<earned> out of <total> 🏆"); else a play time above zero; else no box. A "0 min" play time is never shown. Play time tracked from sessions is labelled "Play time since <date>".
- The library count "with playtime" counts only games with a play time above zero.
- Startup log and `/status` report the real add-on version (the constant was left at 0.9.3).

### Added
- Trophy tiles (Platinum, Gold, Silver, Bronze) are buttons: tap one to list that tier's trophies with "Earned" (and the date) or "Not earned". New endpoint `GET /games/<id>/trophies?player=<key>` reads the full list from PSN with the existing token and Online ID, and saves it in `/data/trophy_lists/` (reused for 15 minutes; the last saved list is shown, marked as such, when a lookup fails). Without PSN lookups the list holds only the earned trophies whose names were recorded locally, and says that it is incomplete and why.
- PSN play time from play sessions (`psn_sessions`): runs with `psn_trophies`, reads the now-playing sensor, sums session start to end, and writes `/data/psn_playtime_<player>.json`. A platform-reported play time is never overwritten. `app/tools/psn_backfill.py` is a one-off, read-only backfill from the recorder database.
- Game tools can offer `progress(tc, player) -> {"done", "total"[, "label"]}` to fill the stat box. No bundled tool reports quest progress yet.
- `GET /library` adds `card_stat` to each game.

## [0.10.4] - 2026-11-11

### Fixed
- 0.10.3 added the cleaned title as a new game before renaming the old one, leaving duplicates. The feed now compares cleaned titles, and a leftover duplicate made by the trophy-title feed is removed when the clean-titled game already exists.

## [0.10.3] - 2026-11-11

### Changed
- Game titles lose the trophy-list wording PSN adds ("X Trophies", "X Trophy Set", "X Veteran's Awards"). New games are cleaned as they arrive; existing entries are renamed once on the next refresh (ids kept; skipped when a same-platform game already has the clean title). Trophy totals still match.

## [0.10.2] - 2026-11-11

### Changed
- Games without a tool no longer get one to-do each. One standing to-do, "Games: check for new games" (due in 7 days), lists them; new games are added to its description, and a new item is created only when none is open.

## [0.10.1] - 2026-11-11

### Changed
- Tests only: replaced real-looking names in the PSN test fixtures with neutral placeholders. No behaviour change.

## [0.10.0] - 2026-11-10

### Added
- New provider `psn_trophy_titles` (add it to `providers_enabled`): one PSN call every 6 hours lists every game with at least one earned trophy.
- Those games are added to the player's library (platform PSN) if no entry with the same title exists. Hidden games stay hidden, nothing is removed, and these bulk-added games do not create "research a tracker" to-dos.
- Games page: a Trophies block in the game details (percent complete, tier counts, latest named trophies). The game summary text gets a trophy line.

## [0.9.9] - 2026-11-10

### Fixed
- PSN trophy names: read the trophy groups from the `TrophyGroupsSummary` object psnawp returns. It was treated as a list and failed with a TypeError. Checked against a live account.

## [0.9.8] - 2026-11-10

### Fixed
- PSN trophy names: pass the platform to psnawp as its `PlatformType` value instead of text. With text, PS5 games were looked up on the PS4 service and PSN answered "not found". Confirmed against a live account.

## [0.9.7] - 2026-11-10

### Fixed
- PSN trophy names: read trophies through the account the NPSSO token belongs to when it matches `psn_online_id`, instead of a public Online ID lookup that returned "not found" for the owner.

## [0.9.6] - 2026-11-10

### Fixed
- The library API now decodes percent-encoded game names in the URL path, so a title with spaces (for example "Cyberpunk 2077") is found instead of returning "game not in library".

## [0.9.5] - 2026-11-10

### Fixed
- Cover art now fills the full height of the card. Landscape art (such as Steam headers) is centred over a blurred copy of itself instead of leaving empty bands above and below.

## [0.9.4] - 2026-11-10

### Fixed
- Removed the gap between the cover art and the divider line when a game card is expanded.

## [0.9.3] - 2026-11-10

### Changed
- Artwork search asks the AI Layer for portrait images only first (so IGDB and other portrait sources get a turn), then asks again for any shape if nothing was found. Needs IH AI Layer 0.5.1 for the portrait filter; older layers ignore it.

## [0.9.2] - 2026-11-10

### Changed
- Cover art linked to igdb.com (the IGDB game database, via IH AI Layer) is accepted as a source.

## [0.9.1] - 2026-11-10

### Fixed
- Upload dialog: pressing Upload re-ran the Yes step, which emptied the file picker, so nothing was uploaded.

## [0.9.0] - 2026-11-10

### Added
- When a cover-art search finds nothing, a dialog offers to upload your own artwork. Choose Yes, pick a JPEG, PNG or WebP image (up to 6 MB) and it is saved as that game's cover. The file type is checked from the image itself, not its name.
- Pressing Refresh on a cover that already exists now searches again and requires a portrait image (the shape is read from the image itself). If none is found, the same dialog offers an upload.
- A first search still prefers portrait but accepts a landscape image if that is all there is.

## [0.8.2] - 2026-11-10

### Changed
- Artwork search tells IH AI Layer which sites it accepts art from, so helpers return only usable results and the layer falls back to the next helper when one has none (needs IH AI Layer 0.4.20).
- Art limits and timeouts are named constants.

## [0.8.1] - 2026-11-10

### Changed
- Artwork search sends only the game title and platform to IH AI Layer, which routes it to the best available helper.
- Tries each returned image until one downloads.
- Shows the real reason when a search fails (daily limit, no key, timeout).

## [0.7.9] - 2026-11-10

### Added
- Import owned Steam games into each player's library, including Steam playtime and last-played data where available. Sync runs at most once per day.
- Uses the existing private Steam API key and profile URL/SteamID64 options; no separate Steam-library setup or credential is needed.

## [0.7.8] - 2026-11-09

### Added
- Optional Steam achievement-name provider using the official Steam Web API, with a private API-key option and per-player SteamID64.
- Quietly baselines existing unlocks, caches game schemas, and records future named unlocks in the normal Games feed and permanent history.
- Setup instructions in STEAM_ACHIEVEMENT_SETUP.md.

## [0.7.7] - 2026-11-09

### Added
- Permanent PSN trophy history: every named trophy is also appended to `/data/trophy_history_<player>.jsonl` (id, game, tier, name, earned time), so it is kept after the 50-event feed limit and across restarts. No credential is written.

## [0.7.6] - 2026-11-09

### Added
- Optional PlayStation trophy-name lookups using a privately configured NPSSO token and each player's PSN Online ID.
- Named trophy events replace aggregate sensor announcements for the same game and tier.
- Setup instructions in PSN_TROPHY_SETUP.md. The provider establishes a quiet baseline and does not announce older trophies on first configuration.

## [0.7.5] - 2026-11-05

### Changed
- The add-on page has a sticky top bar: "‹ Intelligent Home" goes back to the Operations console in the whole Home Assistant window. The footer link also opens in the whole window now (it used to open nested inside the page frame).

## [0.7.4] - 2026-11-04

### Changed
- Remove tracker names and tracker freshness lines from collapsed game cards, including Destiny; keep the complete details and small refresh time by the disclosure control.

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.8.0] - 2026-11-10

### Changed
- Merge exact matching game titles from different platforms into one library card with a pill for each platform.
- Keep platform filters useful for merged cards, and combine known playtime across platform entries.
- Explain when game artwork search has no available provider or the AI Layer is still in shadow mode.

## [0.7.3] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs.

## [0.7.2] - 2026-11-04

### Changed
- Show tracker freshness on each game card as current at last check, stale, unavailable, or unknown, with provider update time where supplied.
- Use the shared IH freshness pattern while preserving game-specific detail actions and refresh behavior.

## [0.7.1] - 2026-11-04

### Added
- Arc Raiders card: collapsed "Done quests (N)" list under Open quests; untick a quest to mark it not done. Details include `done_quests`.

### Changed
- No Undo toast (untick a quest in Done quests instead). The tracker freshness line on game cards is replaced by a small "refreshed HH:MM" next to the expand arrow.

## [0.7.0] - 2026-11-04

### Added
- Arc Raiders card: tick boxes for open quests with a short Undo, hideout bench and project steppers, and "I have" number boxes for materials. Each save re-loads the details so needs and next quests update.
- Details now carry quest ids, hideout benches (level and max), projects (active, phases) and item ids with counts.
- New checked commands `undone`, `hideout_set` and `project_set`; existing chat commands are unchanged.
- The UI proxy forwards `POST /api/games/<id>/cmd` with the same admin-only ingress access as the rest of the UI.

## [0.6.15] - 2026-11-04

### Changed
- Map `/share` read-only so `arc_data_dir` can point at game data kept in the share (for example `/share/arc_raiders`).

## [0.6.14] - 2026-11-04

### Changed
- Add library search, platform filters and sorting by last played, title or playtime.
- Remember expanded game cards and library preferences per player during the page session.
- Clarify tracker freshness and Research categories, review status and next steps.
- Adopt shared UI layout and freshness patterns, with a documented consistency rollout plan.

## [0.6.13] - 2026-11-04

### Changed
- Split each game card into a top-aligned summary and a separate full-width details section; keep neighbouring cards at their own height.
- Remove the artwork colour extension and use a top-right chevron with accessible show/hide labels.
- Reduce refreshed timestamp text and add spacing between game detail panels.
- Replace empty Coming up panels with a compact message; retain the event panel when entries exist.

## [0.6.12] - 2026-11-04

### Changed
- Use a compact details toggle and extend the artwork bottom-edge colour down the cover column behind detail panels.
- Place a faint cover-art action above the top-left of the artwork while preserving its touch target and accessible label.
- Keep a neutral column colour when artwork cannot be sampled or loaded.

## [0.6.11] - 2026-11-04

### Changed
- Compact the library header and statistics, preserve full cover artwork, and separate Research from player tabs.
- Show service status labels on mobile and simplify dates, playtime, and tracker names.
- Place a small translucent artwork action above the top-left of each cover, with a clear keyboard focus state and a 44px touch target.

## [0.6.10] - 2026-11-03

### Changed
- UI layout and style refresh from the Codex pull request (no behaviour change); version bumped so the image builds.

## [0.6.9] - 2026-11-03

### Added
- Let each game module provide a lazy-loaded UI component for its own detail view; move Destiny details and Hunter/Titan/Warlock objective tabs into the Destiny component, and Arc Raiders details into its own component.
- Reuse saved cover art for the same title across player/platform game IDs, without showing image source attribution in the UI.

### Changed
- Keep the shared page focused on the library shell and load game-specific detail UI only when opened.

## [0.6.8] - 2026-11-03

### Changed
- Redesign the Games UI as a player-focused library using the shared IH component library and tokens, with responsive cover-led game cards, clearer metrics, and mobile detail tables.

### Fixed
- Count known play time from the library's `playtime_min` field.

## [0.6.7] - 2026-11-03

### Fixed
- Load already-saved official cover art from the persistent artwork store when rendering each player's library.
- Add a metadata lookup for saved artwork, bypass stale image caches, and show a useful status if a stored image cannot load.

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
