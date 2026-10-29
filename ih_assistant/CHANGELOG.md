# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.0] - 2026-10-29

### Added
- Several personas in one add-on, each with its own separate datastore folder (profile, state, inbox, outbox log). No call reads or writes another persona's files.
- Nameless start: an empty profile, a seed-profile endpoint and an intro event before the first outgoing action.
- Discord transport per persona bot (private messages in, replies and typing out), with rate-limit (429) handling.
- Check-in events at configured times, outside quiet hours and under a daily limit.
- Persona-to-persona messages (`tell`), only to keys listed in the sender's `can_tell`, 20 a day per pair.
- `ask` (question with tick, cross and question reactions; the person's reactions come back as inbox events) and a typing keeper that ends with a `still_waiting` event.
- Dry-run by default: nothing is sent unless `mode` is `live` and the persona's `live` is true.
