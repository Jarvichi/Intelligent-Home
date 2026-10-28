# Intelligent Home - Discord - AI Bot (`ih_discord_ai_bot`)

A **module** of the `ih_discord` transport (0.8.0 or newer). It answers questions typed in chosen Discord channels with the Intelligent Home **AI layer**.
The AI layer knows nothing about Discord: this add-on takes the message, sends its text to the AI layer's HTTP API (`POST /request`, bearer `api_token`),
and posts the answer back as a reply through the transport's `post` operation.

It holds no Discord token, no channel number and no AI key. It talks to the transport over the signed MQTT bus.
**v1 = transport channels only.** Direct messages and private channels are not on the bus and are out of scope.

## Rules it follows

- Answers only users in `allowed_user_ids` (empty list = nobody). The transport's own allow-list must also pass the message.
- One answer per message (message ids are remembered on disk; a restart never answers twice; a crash may lose one answer, never double it).
- Global rate limit `rate_per_min`; extra questions are ignored (counted on `/status`).
- Ignores bot messages and its own posts. Ignores messages older than `max_age_s`.
- If the AI layer is in shadow, down, refused the token, or has no agent, it posts ONE short plain error. It is never silent.
- Kill switch: create the file `/data/STOP` to ignore every message. `/data/STOP_LIVE` forces shadow (nothing posted) at once.
- Message text and answers are never written to the log or `/status`.

## Shadow mode (default)

`shadow: true`: the AI layer **is** asked (it may cost one agent call), but the post is sent with `dry: true` and logged as `[DRY] would post ...`.
Nothing appears in Discord. `/status` shows `would_total`. Turn `shadow` off only after the transport row is live.

## Setup (needs the transport options first; the transport row is the real switch)

1. In the `ih_discord` options add a `modules` row with the **`post`** operation, for example
   `{"name":"ih_discord_ai_bot","signing_key":"<secret>","channels":["<key>"],"ops":["post"],"rate_per_min":30,"shadow":true}`, and make sure `<key>` is a channel key.
2. Create an MQTT user for this module; use the same signing key here and in the transport row.
3. Set `channel_keys`, `allowed_user_ids`, and `api_token` (the same value as the AI layer's `api_token`).
4. Start the add-on. Check `http://<host>:8803/status`: `bus.connected` true, `bus.last_heartbeat_s` small.

A channel can have only ONE catch-all module. If another module already owns the channel key, set `trigger_prefix` (for example `ai `): the bot then claims only
messages that start with it and strips it before asking.

## Options

| Option | Meaning | Default |
|---|---|---|
| `module_name` | Name on the bus. Must match the transport row | `ih_discord_ai_bot` |
| `signing_key` | Secret shared with the transport row | empty (inactive) |
| `shadow` | Ask the AI layer but post nothing | `true` |
| `channel_keys` | Channel KEYS to listen on. Empty = claims nothing | empty list |
| `channels_list` | Optional comma-separated channel KEYS (e.g. `ask, shopping`), merged with `channel_keys`. Trimmed, lowercased, deduplicated, max 20 items of 64 chars. Keys only: the transport routes by key, so raw Discord channel ids are not resolved | empty |
| `trigger_prefix` | If set, only messages starting with it are answered (specific claim) | empty (catch-all) |
| `allowed_user_ids` | Discord user ids that get answers. Empty = nobody | empty list |
| `ai_layer_url` | AI layer address (the shipping config sets it) | empty (error reply) |
| `api_token` | Bearer token of the AI layer | empty (error reply) |
| `capability` | Fixed capability to ask for; empty = the layer classifies | empty |
| `rate_per_min` | Questions accepted per minute (1 to 60) | `6` |
| `max_age_s` | Ignore messages older than this | `300` |
| `ai_timeout_s` | Wait for the AI layer this long | `60` |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | The broker | `core-mosquitto`, `1883`, `ih_discord_ai_bot`, empty |
| `ingest_url`, `ingest_token` | Orchestrator status push (optional) | empty token = no push |
| `log_payloads` | false = each handled Discord message or reaction logs ONE line with metadata only (id, source, what, chars in/out, ms, ok, why). true = the line also holds the first 200 chars of the request and the reply (one line, newlines escaped). Keys and tokens are never logged. | `false` |

`GET /healthz` and `GET /status` on port 8803 need no token and show no secrets or text.

## Untested

Docker build, real MQTT, real Discord, a real AI layer (tests use a fake bus and a fake AI layer). The sync script (`sync_from_service.sh`) copies `bus_client.py` and the manifest from `ih/`.
