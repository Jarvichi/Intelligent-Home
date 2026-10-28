# Intelligent Home - Discord - AI Bot

## What it does

Discord AI bot: answers messages from allow-listed users on chosen channel keys (bus module of ih_discord) by asking the AI layer API and posting the reply. Shadow mode by default: logs what it would post, posts nothing. Module of the `ih_discord` transport.

## Requires

- `ih_discord` add-on
- `ih_ai_layer` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Discord - AI Bot**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `signing_key`, `api_token`, `mqtt_password`, `ingest_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `module_name` | No | Name on the bus. Must match the transport row. Default: `ih_discord_ai_bot`. |
| `signing_key` | Yes | Secret shared with the transport row |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `channel_keys` | No | Channel KEYS to listen on. Empty = claims nothing |
| `channels_list` | No | Optional comma-separated channel KEYS (e.g. |
| `trigger_prefix` | No | If set, only messages starting with it are answered (specific claim) |
| `allowed_user_ids` | No | Discord user ids that get answers. Empty = nobody |
| `ai_layer_url` | No | AI layer address (the shipping config sets it). Default: `http://172.30.32.1:8801`. |
| `api_token` | Yes | Bearer token of the AI layer |
| `capability` | No | Fixed capability to ask for; empty = the layer classifies |
| `rate_per_min` | No | Questions accepted per minute (1 to 60). Default: `6`. |
| `max_age_s` | No | Ignore messages older than this. Default: `300`. |
| `ai_timeout_s` | No | Wait for the AI layer this long. Default: `60`. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_discord_ai_bot`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `ingest_url` | No | Orchestrator status push (optional). Default: `http://172.30.32.1:8790`. |
| `ingest_token` | Yes | Orchestrator status push (optional) |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

The text of messages from allow-listed users on the chosen channels is passed to the AI layer, which may send a scrubbed copy to an outside AI service. Replies are posted through Discord.

## More details

### Rules it follows

- Answers only users in `allowed_user_ids` (empty list = nobody). The transport's own allow-list must also pass the message.
- One answer per message (message ids are remembered on disk; a restart never answers twice; a crash may lose one answer, never double it).
- Global rate limit `rate_per_min`; extra questions are ignored (counted on `/status`).
- Ignores bot messages and its own posts. Ignores messages older than `max_age_s`.
- If the AI layer is in shadow, down, refused the token, or has no agent, it posts ONE short plain error. It is never silent.
- Kill switch: create the file `/data/STOP` to ignore every message. `/data/STOP_LIVE` forces shadow (nothing posted) at once.
- Message text and answers are never written to the log or `/status`.

### Shadow mode (default)

`shadow: true`: the AI layer **is** asked (it may cost one agent call), but the post is sent with `dry: true` and logged as `[DRY] would post ...`.
Nothing appears in Discord. `/status` shows `would_total`. Turn `shadow` off only after the transport row is live.

### Setup (needs the transport options first; the transport row is the real switch)

1. In the `ih_discord` options add a `modules` row with the **`post`** operation, for example
   `{"name":"ih_discord_ai_bot","signing_key":"<secret>","channels":["<key>"],"ops":["post"],"rate_per_min":30,"shadow":true}`, and make sure `<key>` is a channel key.
2. Create an MQTT user for this module; use the same signing key here and in the transport row.
3. Set `channel_keys`, `allowed_user_ids`, and `api_token` (the same value as the AI layer's `api_token`).
4. Start the add-on. Check `http://<host>:8803/status`: `bus.connected` true, `bus.last_heartbeat_s` small.

A channel can have only ONE catch-all module. If another module already owns the channel key, set `trigger_prefix` (for example `ai `): the bot then claims only
messages that start with it and strips it before asking.

### Untested

Docker build, real MQTT, real Discord, a real AI layer (tests use a fake bus and a fake AI layer). The sync script (`sync_from_service.sh`) copies `bus_client.py` and the manifest from `ih/`.
