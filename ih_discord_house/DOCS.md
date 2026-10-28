# Intelligent Home - Discord - House

## What it does

House status commands for Discord as a bus module of ih_discord (who is home, alarm, bins, heating, one device). Shadow mode by default: logs what it would post, posts nothing. Module of the `ih_discord` transport.

## Requires

- `ih_discord` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Discord - House**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `signing_key`, `mqtt_password`, `ingest_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `module_name` | No | Name on the bus. Must match the transport row. Default: `ih_discord_house`. |
| `signing_key` | Yes | Secret shared with the transport row for this module |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `channel_key` | No | Channel KEY (not a number) the module claims as catch-all. Default: `house`. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_discord_house`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `ingest_url` | No | Orchestrator status push (optional). Default: `http://172.30.32.1:8790`. |
| `ingest_token` | Yes | Orchestrator status push (optional) |
| `command_event` | No | Home Assistant event fired for each accepted command. Default: `ih_discord_command`. |
| `shopping_list_entity` | No | Used for `shop` and `got ...` typed in the house channel. Default: `todo.shopping_list`. |
| `meal_plan_script` | No | Used for `shop` and `got ...` typed in the house channel |
| `shop_names` | No | Used for `shop` and `got ...` typed in the house channel |
| `aisles` | No | Used for `shop` and `got ...` typed in the house channel |
| `aisle_order` | No | Used for `shop` and `got ...` typed in the house channel |
| `presence` | No | `[{name, entity}]` people and their presence entities |
| `house_entities` | No | `alarm`, `boiler`, `thermostat`, `bin_next`, `bin_days`, `free_power_events`, `free_power_banked` |
| `status_device` | No | `label`, `battery`, `seen` for the status line |
| `aliases` | No | Extra words per command (`status`, `bins`, `free`) |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

Messages and replies travel through Discord via the transport add-on. Home Assistant data is read locally. Nothing else leaves your network.

## More details

### Shadow mode (default)

`shadow: true` (the default) means nothing reaches Discord and nothing is written to Home Assistant. Every operation is sent with `dry: true`,
and the text it WOULD post is logged as `[DRY] would post (reply to <message id>): ...`. Use it to run beside an older house feature and compare.
`/status` shows `would_post_total` and `last_would`. Creating the file `/data/STOP_LIVE` forces the same behaviour at once.

To go live: switch the older feature off first, set `shadow` to false here, and add the module to `live_modules` in the transport (the transport must not have `shadow` on this module's row).

### Setup

1. Install `ih_discord` (transport) with `router_enabled`, `enable_bus` and a `modules` row for this module (see its README; the example row uses a fake key).
2. Create an MQTT user for this module and put the same signing key in both places (here: `signing_key`; in the transport: the module row).
3. Fill in the options below. `options.example.json` has fake values.
4. Start the add-on. Check `http://<host>:8796/status`: `bus.connected` true, `bus.last_heartbeat_s` small.

### Behaviour

- Only users on the transport's allow-list reach this module, except `whoami` (public).
- The answer logic is the shared `features/house.py` in routed mode (the same code as the in-process feature, copied at build time).
- A command is acknowledged before it is acted on (a crash can lose one command, never repeat it).

### Endpoints

`/healthz` (200 when the module is up) and `/status`: version, shadow, `dry_now`, bus connected, last heartbeat, counts of messages in, ops out, rejected, `last_would`. No secrets.
