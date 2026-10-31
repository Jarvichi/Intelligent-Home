# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.7.0] - 2026-10-31

### Added
- Behaviour levels per persona (0 to 10): `emoji_level`, `gif_level`, `checkin_level`, `humour_level`, `formality_level`, `verbosity_level`, `banter_level`, `reminders_level`. Values are clamped; the same generic defaults for every persona (emoji 2, gif 5, others 5).
- Persona option `fixed_rule` (a fixed prompt line) and top-level options `usage_sensor_session`, `usage_sensor_week`, `usage_sensor_session_reset`, `usage_sensor_week_reset`.
- `GET /persona/<key>/behaviour`: levels, ready-made prompt block, a fresh `gif_ok` decision, emoji cap and check-in cap.
- Hard gates: `checkin_level` 0 = no check-ins, otherwise scales the daily cap and the planned gap; `emoji_level` caps emoji in each reply.

## [0.6.0] - 2026-10-31

### Added
- Check-in day rules per persona (for a college student): `checkin_days` (for example `tue`, `fri`; empty = every day) and `closed_dates` (`YYYY-MM-DD` or `YYYY-MM-DD..YYYY-MM-DD`; no check-in on those dates). Applies to fixed `checkins` and planned ones.
- Check-in texts: `checkin_texts` entries `day|text` (`tue|...`, or `*|...` for any day) put a suggested `text` in the `checkin_due` event (`sent: false`). With `checkin_send: true` and the persona live, the add-on sends the text itself (`sent: true`) so the host does not write one. Dry-run only logs the outbox.
- Usage auto-reply: with `usage_text` (and `usage_text_notime`) set, a message from the person while Claude usage is out (session or week sensor at 99 percent or more; bad sensors count as not out) gets ONE fallback reply per 10 minutes. `{t}` is the reset time (London time; day name when more than 12 hours away). Needs Home Assistant states. Kill switch: file `STOP_AUTOREPLY` in `/data`. Dry-run logs only.

## [0.5.1] - 2026-10-29

### Fixed
- Files the person sends (screenshots, photos) can be read again. A message event now lists its files (`files`: number, safe name, size, type), and `GET /persona/<key>/attachment/<message id>/<n>` returns the file, fetched fresh from Discord each time (links expire). Only the person's own messages, only Discord's file hosts, at most 10 files of 25 MB each.

## [0.5.0] - 2026-10-29

### Added
- Meal times from the orchestrator facts store. Options `facts_url` (the orchestrator API address) and `facts_key` (this add-on's fact client key), and per persona `meal_fact` (for example `cooking/eat_time`). When set, the planner reads that fact (5 s timeout) and avoids the meal like the `meal_plan_entity` eat time (it wins when both are set). The value is `HH:MM` or `{"date": "YYYY-MM-DD", "eat": "HH:MM"}`; a fact for another date is ignored. A missing or unreadable fact means the default meal windows and one log line. The fact is read again when a check-in falls due, so a cook started after the day's plan was made still blocks a check-in at meal time.

## [0.4.0] - 2026-10-29

### Added
- Check-in planner per persona (option `checkin_plan`, default off). Each day it plans `checkins_per_day` times inside `wake_start`..`wake_end`: agreed times first, then random times that avoid timed events in `calendars`, meal times (fixed windows, or the eat time in `meal_plan_entity`) and quiet hours, at least `min_gap_h` apart. A due time queues `checkin_due` `{planned, reason: agreed|random|fixed}` unless it is in quiet hours, over `max_per_day`, within `quiet_if_messaged_h` of the person's last message (from the persona's own inbox), too soon after another check-in, busy, or the person is away (`presence_entity`).
- `POST /persona/<key>/agree` `{date, time}`, `POST /persona/<key>/agree/remove`, `GET /persona/<key>/plan` (times only).
- Fixed `checkins` keep working; their event now also carries `planned` and `reason: fixed`.
- `config.yaml` now has `homeassistant_api: true` (needed to read calendars; the owner is asked to approve this at install or update). If Home Assistant cannot be reached the plan is made without calendars and the log names the error type.
- `checkins_per_day` may be a range such as `2-3` (seeded per date). `busy_windows` / `free_windows` (like `mon-fri 09:45-17:45`) give work-day rules. Random check-ins are also skipped when the persona sent something in the last `quiet_if_messaged_h` hours or its last message is unanswered (from its own outbox and inbox). A plan made while Home Assistant was down is rebuilt (future random times only) once it answers.
- Not ported: the host game follow-up. The `game_session` events from the Games add-on cover it.

## [0.3.0] - 2026-10-29

### Added
- `POST /persona/<key>/event`: other add-ons hand a persona an event (`game_session`, `game_reset`, `reminder_due`) with flat `facts` and an optional `suggested_text`. It lands only in that persona's own inbox. 30 a day per persona and source (429). The log holds the type and lengths only.

## [0.2.0] - 2026-10-29

### Added
- `POST /persona/<key>/identity`: a persona can change its own username, avatar and server nickname (nickname in every server its bot is in). The new username is stored in the persona profile. Username changes are limited to 2 an hour per persona (429 before any Discord call). Dry-run only logs lengths.

## [0.1.0] - 2026-10-29

### Added
- Several personas in one add-on, each with its own separate datastore folder (profile, state, inbox, outbox log). No call reads or writes another persona's files.
- Nameless start: an empty profile, a seed-profile endpoint and an intro event before the first outgoing action.
- Discord transport per persona bot (private messages in, replies and typing out), with rate-limit (429) handling.
- Check-in events at configured times, outside quiet hours and under a daily limit.
- Persona-to-persona messages (`tell`), only to keys listed in the sender's `can_tell`, 20 a day per pair.
- `ask` (question with tick, cross and question reactions; the person's reactions come back as inbox events) and a typing keeper that ends with a `still_waiting` event.
- Dry-run by default: nothing is sent unless `mode` is `live` and the persona's `live` is true.
