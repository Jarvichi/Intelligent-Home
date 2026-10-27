# Intelligent Home House (`ih_discord_house`)

House status commands for Discord, as a **module** of the `ih_discord` transport. It answers short typed commands in one channel:
who is home, alarm state, doors, lights, heating, bin days, one device's battery, and `help`.

It does **not** hold a Discord token and never sees a channel number. It receives messages for a channel **key** over a signed MQTT bus
and answers with a `post` operation; the `ih_discord` transport does the real posting. It reads Home Assistant with its own Supervisor token.

## Shadow mode (default)

`shadow: true` (the default) means nothing reaches Discord and nothing is written to Home Assistant. Every operation is sent with `dry: true`,
and the text it WOULD post is logged as `[DRY] would post (reply to <message id>): ...`. Use it to run beside an older house feature and compare.
`/status` shows `would_post_total` and `last_would`. Creating the file `/data/STOP_LIVE` forces the same behaviour at once.

To go live: switch the older feature off first, set `shadow` to false here, and add the module to `live_modules` in the transport (the transport must not have `shadow` on this module's row).

## Setup

1. Install `ih_discord` (transport) with `router_enabled`, `enable_bus` and a `modules` row for this module (see its README; the example row uses a fake key).
2. Create an MQTT user for this module and put the same signing key in both places (here: `signing_key`; in the transport: the module row).
3. Fill in the options below. `options.example.json` has fake values.
4. Start the add-on. Check `http://<host>:8793/status`: `bus.connected` true, `bus.last_heartbeat_s` small.

## Options

| Option | Meaning | Default |
|---|---|---|
| `module_name` | Name on the bus. Must match the transport row | `ih_discord_house` |
| `signing_key` | Secret shared with the transport row for this module | empty (inactive) |
| `shadow` | Log only, post nothing | `true` |
| `channel_key` | Channel KEY (not a number) the module claims as catch-all | `house` |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | The broker | `core-mosquitto`, `1883`, `ih_discord_house`, empty |
| `ingest_url`, `ingest_token` | Orchestrator status push (optional) | empty token = no push |
| `command_event` | Home Assistant event fired for each accepted command | `ih_discord_command` |
| `presence` | `[{name, entity}]` people and their presence entities | none |
| `house_entities` | `alarm`, `boiler`, `thermostat`, `bin_next`, `bin_days`, `free_power_events`, `free_power_banked` | all empty (feature not offered) |
| `status_device` | `label`, `battery`, `seen` for the status line | empty |
| `aliases` | Extra words per command (`status`, `bins`, `free`) | none |
| `shopping_list_entity`, `meal_plan_script`, `shop_names`, `aisles`, `aisle_order` | Used for `shop` and `got ...` typed in the house channel | generic defaults |

The option names are the same as in `ih_discord`, so values can be copied over.

## Behaviour

- Only users on the transport's allow-list reach this module, except `whoami` (public).
- The answer logic is the shared `features/house.py` in routed mode (the same code as the in-process feature, copied at build time).
- A command is acknowledged before it is acted on (a crash can lose one command, never repeat it).

## Endpoints

`/healthz` (200 when the module is up) and `/status`: version, shadow, `dry_now`, bus connected, last heartbeat, counts of messages in, ops out, rejected, `last_would`. No secrets.
