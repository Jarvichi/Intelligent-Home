# Intelligent Home - Discord - Shopping

## What it does

Shopping list commands for Discord as a bus module of ih_discord (add items, shop checklist by aisle with tick reactions, got ...). Shadow mode by default: logs what it would post, posts nothing. Also the iPhone shopping Shortcut endpoint (dry by default). Module of the `ih_discord` transport.

## Requires

- `ih_discord` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Discord - Shopping**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `signing_key`, `mqtt_password`, `ingest_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `module_name` | No | Name on the bus. Must match the transport row. Default: `ih_discord_shopping`. |
| `signing_key` | Yes | Secret shared with the transport row for this module |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `channel_key` | No | Channel KEY (not a number) of the shopping channel (catch-all claim). Default: `shopping`. |
| `house_channel_key` | No | Optional second channel KEY where only `shop` and `got ...` are claimed (plain text answers). Empty = not used |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_discord_shopping`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `ingest_url` | No | Orchestrator status push (optional). Default: `http://172.30.32.1:8790`. |
| `ingest_token` | Yes | Orchestrator status push (optional) |
| `command_event` | No | Event fired for `shop` / `got ...` typed in the house channel. Empty = none. Default: `ih_discord_command`. |
| `shopping_list_entity` | No | The Home Assistant to-do entity. Default: `todo.shopping_list`. |
| `shortcut_token` | No | Setting `shortcut_token`. |
| `shortcut_dry` | No | Setting `shortcut_dry`. Default: `true`. |
| `shortcut_base_url` | No | Setting `shortcut_base_url`. |
| `meal_plan_script` | No | Script (name without `script.`) run quietly before a list is built. Empty = none |
| `shop_names` | No | Extra shop names for "I'm at <shop>", words separated by `\ |
| `aisles` | No | Departments (`name`, `words` separated by `\ |
| `facts_url` | No | Orchestrator API address (for example `http://<host>:8790`). With `facts_key`: the checklist uses the learned shop order from fact `shopping/aisle_learned` and adds a use-by date, like `(Tue 3 Nov)`, to fresh things from fact `cooking/fresh_dates`. Empty = off, the checklist is as before. Each read waits 5 s at most; a failure gives today's output. |
| `facts_key` | No | This add-on's `fact_clients` key (module `shopping`, write false) in the orchestrator. |
| `fresh_aisles` | No | Departments that get a date. Default: Meat & fish, Fruit & veg, Dairy & chilled, Bakery. Empty list = no dates. |
| `aisle_order` | No | Departments (`name`, `words` separated by `\ |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

Messages and replies travel through Discord via the transport add-on. Home Assistant data is read locally. Nothing else leaves your network.

## More details

### Shadow mode (default)

`shadow: true` (the default) means nothing reaches Discord and nothing is written to Home Assistant. Every operation is sent with `dry: true`,
and what it WOULD do is logged as `[DRY] would post ...`, `[DRY] would react ...`, `[DRY] would watch ...`. Use it to run beside an older shopping feature and compare.
`/status` shows `would_total` and `last_would`. The file `/data/STOP_LIVE` forces the same behaviour at once.

To go live: switch the older shopping feature off first, set `shadow` to false here, and add the module to `live_modules` in the transport (the transport must not have `shadow` on this module's row).

### Reactions

1. `shop` posts a question (tag `scope`): tap 1 for the full list, 2 for the extras only. Only the person who asked can answer. No answer in 180 s: the full list is posted.
2. The checklist is posted as one message per aisle (tag `checklist`), with a number reaction per line. Old checklist messages are deleted first.
3. Tapping a number completes the item in Home Assistant and edits the line to ~~struck-through~~.

The transport sends the reaction events for watched messages to this module only. A watch lasts at most one hour; the module renews it while the list is young.

### Setup

1. Install `ih_discord` (transport) with `router_enabled`, `enable_bus` and a `modules` row for this module. The row needs both channel keys, and `delete_tagged` in `ops`.
   Example (fake key): `{"name":"ih_discord_shopping","signing_key":"<secret>","channels":["shopping","house"],"ops":["post","react","edit","delete","delete_tagged","watch","unwatch"],"rate_per_min":60,"shadow":true}`
2. Create an MQTT user for this module and put the same signing key in both places (here: `signing_key`; in the transport: the module row).
3. Fill in the options below. `options.example.json` has fake values.
4. Start the add-on. Check `http://<host>:8795/status`: `bus.connected` true, `bus.last_heartbeat_s` small.

### Behaviour

- Only users on the transport's allow-list reach this module.
- A command is acknowledged before it is acted on (a crash can lose one command, never repeat it).
- The answer text is the shared `features/shopping.py` (the same code as the in-process feature, copied at build time).
- Receipts (photos) are not supported on the bus.

### Endpoints

`/healthz` (200 when the module is up) and `/status`: version, shadow, `dry_now`, bus connected, last heartbeat, counts of messages and reactions in, ops out, rejected, open scope question, open checklists, `last_would`. No secrets.

### iPhone Shortcut endpoint

The same add-on serves the endpoint the iPhone Shortcut posts its Reminders to (it replaces the old Home Assistant webhook).
`POST http://<host IP>:8795/shortcut/<shortcut_token>`. The token in the path is the only secret; use a long random value. `shortcut_token` empty = the endpoint is off (404).

- Body: JSON `{"items": ["milk", "eggs"]}` (or one string with line breaks), form field `items=` (one per line), or plain text (one per line). Empty lines and repeats are dropped.
- Items already open on the list (any letter case) are skipped. The rest are added with `todo.add_item` on `shopping_list_entity`.
- Reply `200 {"status":"ok","added":[...],"duplicates_skipped":N,"total_items":N}`; `400 {"error":...}` for bad or empty input; `502` if Home Assistant does not answer.
- `shortcut_dry: true` (the default) changes nothing and the reply adds `"dry": true`. The file `/data/STOP_LIVE` forces dry at once. This is separate from `shadow` (Discord).
- The log shows counts only, never item text. `/status` has a `shortcut` block (enabled, dry, requests).
- Keep the port on the home network only: anyone who knows the token can add items.
