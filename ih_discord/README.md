# Intelligent Home Discord (ih_discord) - the transport

A small Home Assistant add-on that is the **Discord link** of Intelligent Home. It holds the one bot token, reads each channel once, and lets separate **feature add-ons** (modules, for example `ih_house` and `ih_discord_shopping`) receive messages and post, react, edit and delete through signed MQTT messages. It has **no commands of its own** (since 0.7.0).

Nothing about your home is in the code. It maps no folders, stores no secrets in code, and starts in **dry-run**: modules get result code `dry` and nothing is posted until you go live.

## 0.7.0: features moved out
Until 0.6.x this add-on also ran the shopping-list and house-status commands in-process. They now live in their own add-ons (`ih_discord_shopping`, `ih_house`). The old options of those features (`live_features`, `retired_features`, `old_service_disabled`, `shopping_list_entity`, `meal_plan_script`, `shop_names`, `aisles`, `aisle_order`, `command_event`, `presence`, `house_entities`, `status_device`, `aliases`, `brain_webhook_id`, `brain_url`, `channels.brain_inbox`) and the switches `router_enabled` / `enable_bus` are still **accepted** so saved options keep validating after the update, but they are **ignored** (shown in `/status` as `deprecated_options_ignored`). The router and the bus are always on. Move those settings to the module add-ons. The brain-inbox plugin was removed.

## Install (local add-on)
Copy this folder to your local add-ons share, reload the add-on store, install, fill in the options, start. The add-on never installs or restarts anything by itself.

## Going live
A module's Discord writes are real only when ALL of these are true: `dry_run` is off, the module is in `live_modules`, and neither `/data/STOP_LIVE` nor `/data/STOP_LIVE_<module>` exists (kill switch: create it and writes are dry again within seconds). `set_live.py live|dry` flips `dry_run` (needs `SUPERVISOR_TOKEN`).

| Option | Meaning | Example |
|---|---|---|
| `discord_token` | Bot token (secret) | (your token) |
| `dry_run` | Log only, never post or change | `true` |
| `bot_name` | Name shown in `/status` | `"Discord module"` |
| `channels.shopping` | Shopping channel: a name or a channel number | `"groceries"` |
| `channels.house` | House channel (empty = house commands off) | `"123456"` |
| `allowed_user_ids` | Discord user ids allowed to give commands | `["1111"]` |
| `ingest_url` | Status page add-on address (default = the Supervisor gateway `172.30.32.1`, port 8790) | `"http://172.30.32.1:8790"` |
| `ingest_token` | Token for the status page (secret) | |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | Scheduler bus (default host is the standard Mosquitto add-on) | `core-mosquitto`, `1883` |
| `enable_scheduler`, `signing_key` | Scheduled self-check (off by default; needs a signing key) | `false` |
| `max_age_s` | Router: messages older than this are skipped after downtime (minimum 60) | `600` |
| `extra_channels` | Router: more channel keys for bus modules: `key`, `id` (a number) | `[{"key":"scratch","id":"789012"}]` |
| `modules` | Bus modules and what they are GRANTED: `name`, `signing_key` (secret, one per module), `channels` (channel keys), `ops`, `rate_per_min`, `public_words` | see below |
| `live_modules` | Bus modules whose Discord writes are real (also needs `dry_run` off). Others get result code `dry` | `["ih_house","ih_discord_shopping"]` |
| `offline_notice` | Post one "module is offline" line (once per channel per 10 minutes) when a message for a silent module arrives | `false` |

## Router and bus (always on since 0.7.0)
Modules never get the token or a channel number, only channel **keys**. The channel keys are `house` (`channels.house`), `shopping` (`channels.shopping`) and the `key`s in `extra_channels`.
- **Router.** The add-on keeps a cursor per channel (`/data/cursor.json`) and a journal (`/data/journal.jsonl`, 24 hours). A restart resumes from the cursor: nothing is answered twice. The first run starts at the newest message. Messages older than `max_age_s` are skipped. Create `/data/CURSOR_TO_NOW` before a start to jump every cursor to the newest message (used when handing a channel over).
- **Claims.** A module claims a channel key with `exact` words, a `prefix`, a `regex` or `any`. All modules with a matching specific claim get the message. If none match, the single catch-all gets it. A second catch-all on one channel is refused. Only users in `allowed_user_ids` reach a module, except for its `public_words`.
- **Grants.** What a module may do is set here, in `modules`, not by the module. A claim or op outside the grant gets `denied_channel` / `denied_op`. Default ops: post, reply, react, unreact, edit, delete, typing, watch, unwatch. A module can edit, delete or watch only messages it created.
- **Signing.** Every bus message carries `v`, `ts` and an HMAC-SHA256 signature made with that module's own key over the whole message. Wrong key, changed content, another module's topic, older than 120 s, or a repeated `op_id` are refused and counted (`/status`, never the content). Keys are never logged. The topics, fields and the client for module authors are in `bus_client.py`.
- **Dry-run.** A bus module writes to Discord only when `dry_run` is off AND it is in `live_modules` AND neither `/data/STOP_LIVE` nor `/data/STOP_LIVE_<module>` exists. `/status` shows the router, every module's last heartbeat and the rejected counts.
- **Shadow module (S2).** A `modules` row with `"shadow": true` gets COPIES of what the live claimant gets, may claim the same channel key as the live catch-all module, and every op it sends is forced dry (result code `dry`). Use it to try a new module beside the live one and compare answers. Example row for the house module: `{"name":"ih_house","signing_key":"<secret>","channels":["house"],"ops":["post","typing"],"rate_per_min":30,"public_words":["whoami"],"shadow":true}`. The channel key `house` is the key of `channels.house`. Cut-over: remove `shadow` (or set it false), add the module to `live_modules`, stop the old module first.
- **Test module.** `tests/ih_echo.py` is a fake module that echoes; the tests run it over an in-memory bus (`bus_client.FakeBus`).

## Sources
`core.py`, `settings.py`, `manifest.json`, `schedule_client.py` and `bus_client.py` are COPIES made by `sync_from_service.sh`. Edit the source, not the copy. The feature code (`features/*.py`) is no longer part of this add-on: its single source stays in the commands service folder and is copied by the module add-ons' own `sync_from_service.sh`.

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder: it fails on house-specific terms, private addresses, long ids, emails, phone numbers and secret values.
