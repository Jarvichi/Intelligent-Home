# Intelligent Home Shopping (`ih_discord_shopping`)

A shopping list in Home Assistant, driven from Discord, as a **module** of the `ih_discord` transport.
In the shopping channel: type items to add them, `shop` for a checklist by aisle (tap a number when you have the item), `got milk, bread` to tick things off, `help`.

It does **not** hold a Discord token and never sees a channel number. It receives messages for a channel **key** over a signed MQTT bus
and answers with operations (`post`, `react`, `edit`, `delete`, `delete_tagged`, `watch`); the `ih_discord` transport does the real work.
It reads and writes the Home Assistant to-do list with its own Supervisor token.

## Shadow mode (default)

`shadow: true` (the default) means nothing reaches Discord and nothing is written to Home Assistant. Every operation is sent with `dry: true`,
and what it WOULD do is logged as `[DRY] would post ...`, `[DRY] would react ...`, `[DRY] would watch ...`. Use it to run beside an older shopping feature and compare.
`/status` shows `would_total` and `last_would`. The file `/data/STOP_LIVE` forces the same behaviour at once.

To go live: switch the older shopping feature off first, set `shadow` to false here, and add the module to `live_modules` in the transport (the transport must not have `shadow` on this module's row).

## Reactions

1. `shop` posts a question (tag `scope`): tap 1 for the full list, 2 for the extras only. Only the person who asked can answer. No answer in 180 s: the full list is posted.
2. The checklist is posted as one message per aisle (tag `checklist`), with a number reaction per line. Old checklist messages are deleted first.
3. Tapping a number completes the item in Home Assistant and edits the line to ~~struck-through~~.

The transport sends the reaction events for watched messages to this module only. A watch lasts at most one hour; the module renews it while the list is young.

## Setup

1. Install `ih_discord` (transport) with `router_enabled`, `enable_bus` and a `modules` row for this module. The row needs both channel keys, and `delete_tagged` in `ops`.
   Example (fake key): `{"name":"ih_discord_shopping","signing_key":"<secret>","channels":["shopping","house"],"ops":["post","react","edit","delete","delete_tagged","watch","unwatch"],"rate_per_min":60,"shadow":true}`
2. Create an MQTT user for this module and put the same signing key in both places (here: `signing_key`; in the transport: the module row).
3. Fill in the options below. `options.example.json` has fake values.
4. Start the add-on. Check `http://<host>:8795/status`: `bus.connected` true, `bus.last_heartbeat_s` small.

## Options

| Option | Meaning | Default |
|---|---|---|
| `module_name` | Name on the bus. Must match the transport row | `ih_discord_shopping` |
| `signing_key` | Secret shared with the transport row for this module | empty (inactive) |
| `shadow` | Log only, post and write nothing | `true` |
| `channel_key` | Channel KEY (not a number) of the shopping channel (catch-all claim) | `shopping` |
| `house_channel_key` | Optional second channel KEY where only `shop` and `got ...` are claimed (plain text answers). Empty = not used | empty |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | The broker | `core-mosquitto`, `1883`, `ih_discord_shopping`, empty |
| `ingest_url`, `ingest_token` | Orchestrator status push (optional) | empty token = no push |
| `shopping_list_entity` | The Home Assistant to-do entity | `todo.shopping_list` |
| `meal_plan_script` | Script (name without `script.`) run quietly before a list is built. Empty = none | empty |
| `shop_names` | Extra shop names for "I'm at <shop>", words separated by `\|` | empty |
| `aisles`, `aisle_order` | Departments (`name`, `words` separated by `\|`) and their order. Empty = generic grocery defaults | defaults |
| `command_event` | Event fired for `shop` / `got ...` typed in the house channel. Empty = none | `ih_discord_command` |
| `log_payloads` | false = each handled Discord message or reaction logs ONE line with metadata only (id, source, what, chars in/out, ms, ok, why). true = the line also holds the first 200 chars of the request and the reply (one line, newlines escaped). Keys and tokens are never logged. | `false` |

The option names are the same as in `ih_discord`, so values can be copied over.

## Behaviour

- Only users on the transport's allow-list reach this module.
- A command is acknowledged before it is acted on (a crash can lose one command, never repeat it).
- The answer text is the shared `features/shopping.py` (the same code as the in-process feature, copied at build time).
- Receipts (photos) are not supported on the bus.

## Endpoints

`/healthz` (200 when the module is up) and `/status`: version, shadow, `dry_now`, bus connected, last heartbeat, counts of messages and reactions in, ops out, rejected, open scope question, open checklists, `last_would`. No secrets.
