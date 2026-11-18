# Intelligent Home - Speaker

## What it does

A small tool that speaks a message on a house speaker (HomePod) through Home Assistant text-to-speech. It does **not** write messages: an assistant decides what to say and sends the text here. Dry-run by default.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised, with a `tts.piper` and/or `tts.kokoro` entity and the speaker as a `media_player` entity.

## Set-up

1. Install **Intelligent Home - Speaker**.
2. Set `api_token` and the lists below, then start it. It starts in dry mode: every request only reports what it would do.
3. Send a request with `dry_run` and read the answer. When it looks right, set `mode` to `live`.

## Options

| Option | What it is |
|---|---|
| `mode` | `dry` (default) plays nothing; `live` speaks. |
| `api_token` | Bearer token callers must send. Empty = everything refused. |
| `timezone` | Time zone for the quiet hours. |
| `default_volume` | Volume while speaking (0.2). The earlier volume is restored afterwards. |
| `quiet_start`, `quiet_end` | Only alerts are spoken in this window (default 00:00 to 07:00). |
| `night_start` | Start of "night" for `night_deny` (default 22:00; it ends at `quiet_end`). |
| `alert_personas` | Personas whose `urgency: alert` passes the quiet hours. |
| `speakers` | `name=media_player.entity` entries. |
| `voices` | `name=Piper voice id` entries (the name can be a persona). Only these voices can be used. |
| `permissions` | `persona=speaker1,speaker2` (or `*`). A persona that is not listed may not speak. |
| `night_deny` | `persona=speaker` entries the persona may not use between `night_start` and `quiet_end`. |
| `keep_volume_speakers` | Speakers whose volume is never touched (a group leader). |
| `engines` | TTS entities in order of preference. The voice option is passed to Piper only. |
| `queue_max`, `queue_ttl_s` | Longest waiting line per speaker, and how long a message may wait. |

Example entries (placeholders): `speakers: ["bedroom=media_player.bedroom_speaker"]`, `voices: ["hearth=voice_id_here"]`, `permissions: ["hearth=*", "guest=kitchen"]`, `night_deny: ["guest=bedroom"]`.

## Calling it

`POST http://<host>:8831/speak` with header `Authorization: Bearer <api_token>` and a JSON body:

```json
{"text": "Hello", "persona": "hearth", "speaker": "bedroom", "voice": "hearth", "volume": 0.2, "urgency": "normal", "skip_if_playing": true, "dry_run": true}
```

The call returns when the message has been spoken (allow up to about two minutes while other messages are ahead). `status` is one of `spoken`, `dry`, `blocked`, `skipped`, `queue_full`, `expired`, `fallback` (play your old clip), `error`. `GET /status` lists recent results (no message text). `GET /healthz` is open.

A file named `STOP` in the add-on data folder refuses every request.

## Not tested here

No real speaker playback has been heard. Test with `dry_run` first, then one real message.
