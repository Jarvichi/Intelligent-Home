# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.0] - 2026-11-18

### Added
- First version: a text-to-speech tool for the house speakers. `POST /speak` takes `text`, `persona`, `speaker` and optional `voice`, `volume` (0 to 1 or `keep`), `urgency` (`alert`), `skip_if_playing` and `dry_run`. It never writes a message: callers (the assistants) do.
- Engine order from the `engines` option (default Piper, then Kokoro). The voice option is passed to Piper only. If neither engine is available, or the speaker never starts playing within 20 s, the answer is `status: fallback` so the caller can play its old clip.
- Volume: set to `default_volume` (0.2), then restored to the earlier level after the speech (wait = max(5, ceil(length / 12) + 3) s). `volume: keep`, or a speaker listed in `keep_volume_speakers` (a group leader), leaves the volume alone.
- One message at a time per speaker, first come first served, with a maximum queue length and a wait limit. A speaker that is already playing is skipped (`status: skipped`) unless the caller says `skip_if_playing: false`.
- Rules callers cannot switch off, set in the options: which persona may use which speaker, speakers a persona may not use at night, quiet hours (only `urgency: alert` from a persona in `alert_personas` passes).
- `dry_run` returns the text, engine, voice, volume plan and allowed or blocked with the reason, and plays nothing. The add-on starts in dry mode, which does the same for every request.
- Why: speaking (voice, volume, queue, quiet rules) is one job; deciding what to say belongs to the assistants.
