# Intelligent Home - Usage Trace

Answers "what used my Claude allowance?". Every 5 minutes it stores the session % and week % from Home Assistant. Callers (the assistants, the work queue, task channels, the inbox sort) post a labelled event when a Claude call starts and ends. The page draws the usage line with the events on top, and flags any rise that no event explains.

Only labels and times are stored. Message text is never sent or stored.

## Requires

- Home Assistant OS or Supervised.
- The Claude usage sensors (session, week, optional week pace).

## Set-up

1. Install **Intelligent Home - Usage Trace**.
2. Set `api_token` to a long random text. Callers use it.
3. Start the add-on. In the Info tab check **Start on boot**, **Watchdog** and **Auto update** are on.
4. Open **IH · Usage Trace** in the sidebar. The line starts filling after 5 minutes.
5. When ready for events to be stored, set `mode` to `live` and restart.

## Options

| Option | Default | Meaning |
|---|---|---|
| `api_token` | empty | Bearer token for the API. Empty = the API refuses everything except `/healthz`. |
| `mode` | `shadow` | `shadow`: series recorded, `POST /events` only logs "would store". `live`: events are stored. |
| `session_entity` | `sensor.claude_usage_session_usage` | 5-hour session usage sensor (percent). |
| `week_entity` | `sensor.claude_usage_week_usage` | Week usage sensor (percent). |
| `pace_entity` | `sensor.claude_usage_week_usage_pace` | Week pace sensor (optional, stored for later). |
| `retention_days` | 60 | Series and events older than this are deleted (daily). |
| `jump_threshold` | 2.0 | A rise of at least this many points in one sample step with no overlapping event is flagged. |

## API (port 8848, `Authorization: Bearer <api_token>`)

- `GET /healthz` (open), `GET /status`
- `POST /events` with `{source, label, start?, end?, ref?}`. `start` defaults to now (ISO 8601 or epoch seconds). A post without `end` opens an event; a later post with the same `source` and `ref` and an `end` closes it. Text fields are trimmed to 80 characters. Unknown fields are refused.
- `GET /events?since=&until=&source=`, `GET /series?since=&until=` (also returns the unattributed jumps)

## Limits

- The sensors update about every 5 minutes, so attribution is by time window: overlapping events share a jump and are shown together.
- Interactive sessions have no hook and show up as unattributed jumps.
- A gap longer than 15 minutes between samples (add-on stopped) is never flagged.
- An event with no end is shown for at most 6 hours.
- The series and events are a convenience record, not a backup.
