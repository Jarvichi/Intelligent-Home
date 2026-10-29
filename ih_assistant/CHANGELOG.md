# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.4.0] - 2026-10-29

### Added
- Check-in planner per persona (option `checkin_plan`, default off). Each day it plans `checkins_per_day` times inside `wake_start`..`wake_end`: agreed times first, then random times that avoid timed events in `calendars`, meal times (fixed windows, or the eat time in `meal_plan_entity`) and quiet hours, at least `min_gap_h` apart. A due time queues `checkin_due` `{planned, reason: agreed|random|fixed}` unless it is in quiet hours, over `max_per_day`, within `quiet_if_messaged_h` of the person's last message (from the persona's own inbox), too soon after another check-in, busy, or the person is away (`presence_entity`).
- `POST /persona/<key>/agree` `{date, time}`, `POST /persona/<key>/agree/remove`, `GET /persona/<key>/plan` (times only).
- Fixed `checkins` keep working; their event now also carries `planned` and `reason: fixed`.
- `config.yaml` now has `homeassistant_api: true` (needed to read calendars; the owner is asked to approve this at install or update). If Home Assistant cannot be reached the plan is made without calendars and the log names the error type.
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
