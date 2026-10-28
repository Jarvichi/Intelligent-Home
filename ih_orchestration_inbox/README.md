# Intelligent Home Orchestration Inbox (`ih_orchestration_inbox`)

A durable **inbox** for chosen Discord channels, as a **module** of the `ih_discord` transport (0.8.0 or newer).
It claims the channel keys you list, writes every message that arrives to a local queue, and puts one emoji (eyes by default) on the message so the
sender knows it arrived. It **never answers in the channel.** Claude or a worker reads the queue over a small token-protected API and marks messages handled.

**v1 = transport channels only.** Direct messages and private channels are NOT on the bus; they stay with the existing watcher scripts.

It holds no Discord token, no channel number and no Home Assistant token. It talks to the transport over the signed MQTT bus.

## What it stores

For each message: `seq`, message id, channel key, author id, time, text (up to 2000 characters), `reply_to`, attachment count and kinds, and whether the
author is on the transport allow-list. JSONL files under `/data/queue` (rotated at 256 KB, whole files removed after `retention_days`, default 7).
The message text is never written to the add-on log or to `/status`. Only allowed users get the emoji and are returned by the API (`all=1` shows the rest).

## Shadow mode (default)

`shadow: true`: the queue **is written**, but the reaction is sent with `dry: true` and logged as `[DRY] would react ...`. Nothing appears in Discord.
`/status` shows `would_total` and `last_would`. The file `/data/STOP_LIVE` forces the same at once.

## Setup (needs the transport options first; the transport row is the real switch)

1. In the `ih_discord` options add a `modules` row for this module with the **`react`** operation, for example
   `{"name":"ih_orchestration_inbox","signing_key":"<secret>","channels":["<key>"],"ops":["react"],"rate_per_min":60,"shadow":true}`,
   and make sure `<key>` exists as a channel key. Without `react` in `ops` the emoji is refused (messages are still queued).
2. Create an MQTT user for this module; put the same signing key here and in the transport row.
3. Set `channel_keys` (for example `["<key>"]`) and an `api_token` (any long random string). Without `api_token` the API refuses everything.
4. Start the add-on. Check `http://<host>:8800/status`: `bus.connected` true, `bus.last_heartbeat_s` small. Leave `shadow` on until the queue looks right, then turn it off.

## Options

| Option | Meaning | Default |
|---|---|---|
| `module_name` | Name on the bus. Must match the transport row | `ih_orchestration_inbox` |
| `signing_key` | Secret shared with the transport row | empty (inactive) |
| `shadow` | Write the queue, but do not react | `true` |
| `channel_keys` | Channel KEYS to claim (catch-all). Empty = claims nothing | empty list |
| `ack_emoji` | Emoji put on each message | eyes |
| `retention_days` | Days to keep the queue (1 to 30) | `7` |
| `api_token` | Bearer token for `/queue` and `/ack`. Empty = refused | empty |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | The broker | `core-mosquitto`, `1883`, `ih_orchestration_inbox`, empty |
| `ingest_url`, `ingest_token` | Orchestrator status push (optional) | empty token = no push |
| `log_payloads` | false = each handled Discord message or reaction logs ONE line with metadata only (id, source, what, chars in/out, ms, ok, why). true = the line also holds the first 200 chars of the request and the reply (one line, newlines escaped). Keys and tokens are never logged. | `false` |

## API (port 8800, header `Authorization: Bearer <api_token>`)

- `GET /queue?since=<seq>&channel=<key>&consumer=<name>&limit=<n>&all=1`: records with `seq` greater than `since`. Without `since` the consumer's cursor is used (default consumer `default`).
- `POST /ack` with `{"seq": N, "consumer": "name"}`: moves that consumer's cursor to N (never backwards, never past the newest). Each consumer has its own cursor.
- `GET /status` and `GET /healthz`: no token, no secrets, no message text.
