# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.3.0] - 2026-11-19

### Added
- Clip cache: every message that is rendered is kept as a file under `/data/cache/<engine>/<voice>/<sha1 of the normalised text>.<ext>` (not part of backups) with an index in the add-on database (`/data/ih.db`: engine, voice, text, label, size, created, last_used, hits, pinned). A request checks the cache first: a hit plays at once, a miss renders, stores and plays. The same phrase under two voices or engines is two entries. The Discord voice clip uses the cache too (a dry run reads it but stores nothing).
- Limits: `cache_mb` (default 1024) caps the total size; after each store the least recently used unpinned clips are deleted until it fits. Unpinned clips not used for `cache_days` (default 30) expire. Pinned clips are never deleted.
- Fillers: `POST /speak` takes `filler=<label>`. The cached phrase plays at once while the real message renders; it must finish (or is stopped) before the real clip starts. If the phrase is not cached for that voice, or playback of cached clips is not set up, the filler is skipped. `seed_fillers` (default on) makes the pinned phrases One moment, Just checking, Alarm armed, Alarm disarmed and Good morning for the engine default voice and each configured voice (at start in live mode, and on first use of a voice).
- `base_url` option: the address a speaker can use to fetch clips from this add-on (for example `http://<home assistant host>:8831`). Clips are served at `/clip/<id>.<ext>` with a signed, 10-minute link (no bearer needed, the signature uses `api_token`). Without it, messages still play through `tts.speak` as before (and are still cached), but fillers and "play on speaker" from the page are not available.
- Ingress page (admin only): table of clips with search, voice and engine filters and Clear, Listen, Play on speaker, Pin or Unpin, Delete, and a box to type a message, pick a voice, then Generate (saved) or Generate and play. In dry mode nothing is played on a speaker.
- `dry_run` answers carry `cache`: hit or miss, clip and cache size, and which clips a store would evict. `GET /status` shows cache totals.

## [0.2.0] - 2026-11-19

### Added
- Voice clip to Discord: `POST /speak` takes `to` (`speaker` default, `discord` or `both`), `discord_user` (a name from `discord_users`, never a raw ID) and optional `caption` (true = the text also goes as a separate message). The audio is rendered by Home Assistant text-to-speech with the same engine and voice as the speaker, without playing anything (no speaker, no volume change, no queue).
- It is sent as a true Discord voice message (round player bubble): ffmpeg converts the audio to ogg/opus (libopus, mono, 48 kHz), the duration and a 256-point waveform are measured, and the message is sent with flag 8192 through the three-step upload. If Discord refuses the voice message, the clip is sent as a plain `voice-message.ogg` attachment (`delivery: attachment`).
- Options `discord_token` (secret), `discord_users` (`name=numeric id`) and `discord_permissions` (`persona=name1,name2` or `*`; a persona that is not listed may not send). Quiet hours do not block a DM (it makes no sound).
- `dry_run` (or dry mode) renders and measures the clip and reports `duration_s`, `audio_bytes`, `waveform_bytes`; it sends nothing.
- ffmpeg added to the Dockerfile. New helper `send_voice_message` in the shared `ihlib/discord.py` (copied into this add-on only; other add-ons keep their copy until their next sync).
- `GET /status` lists the `discord_users` names.

## [0.1.0] - 2026-11-18

### Added
- First version: a text-to-speech tool for the house speakers. `POST /speak` takes `text`, `persona`, `speaker` and optional `voice`, `volume` (0 to 1 or `keep`), `urgency` (`alert`), `skip_if_playing` and `dry_run`. It never writes a message: callers (the assistants) do.
- Engine order from the `engines` option (default Piper, then Kokoro). The voice option is passed to Piper only. If neither engine is available, or the speaker never starts playing within 20 s, the answer is `status: fallback` so the caller can play its old clip.
- Volume: set to `default_volume` (0.2), then restored to the earlier level after the speech (wait = max(5, ceil(length / 12) + 3) s). `volume: keep`, or a speaker listed in `keep_volume_speakers` (a group leader), leaves the volume alone.
- One message at a time per speaker, first come first served, with a maximum queue length and a wait limit. A speaker that is already playing is skipped (`status: skipped`) unless the caller says `skip_if_playing: false`.
- Rules callers cannot switch off, set in the options: which persona may use which speaker, speakers a persona may not use at night, quiet hours (only `urgency: alert` from a persona in `alert_personas` passes).
- `dry_run` returns the text, engine, voice, volume plan and allowed or blocked with the reason, and plays nothing. The add-on starts in dry mode, which does the same for every request.
- Why: speaking (voice, volume, queue, quiet rules) is one job; deciding what to say belongs to the assistants.
