# Intelligent Home - Discord

## What it does

Discord transport: one bot token, one poller per channel, signed MQTT bus for feature modules (ih_discord_house, ih_discord_shopping). No commands of its own. Modules write for real only when dry_run is off and they are in live_modules. Parent of its family.

## Requires

- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Discord**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `discord_token`, `ingest_token`, `mqtt_password`, `signing_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `dry_run` to `false`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `discord_token` | Yes | Bot token (secret) |
| `dry_run` | No | On (default): reads and logs only. Turn off to go live. |
| `live_features` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `retired_features` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `old_service_disabled` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `bot_name` | No | Name shown in `/status`. Default: `Discord module`. |
| `channels` | No | Map of channel keys to Discord channel ids, one `key=id` per line. |
| `allowed_user_ids` | No | Discord user ids allowed to give commands |
| `shopping_list_entity` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `meal_plan_script` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `shop_names` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `aisles` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `aisle_order` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `command_event` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `presence` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `house_entities` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `status_device` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `aliases` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `ingest_url` | No | Status page add-on address (default = the Supervisor gateway `172.30.32.1`, port 8790) |
| `ingest_token` | Yes | Token for the status page (secret) |
| `brain_webhook_id` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `brain_url` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_discord`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `enable_scheduler` | No | Scheduled self-check (off by default; needs a signing key) |
| `signing_key` | Yes | Scheduled self-check (off by default; needs a signing key) |
| `router_enabled` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `enable_bus` | No | Deprecated since 0.7.0: accepted so saved options stay valid, ignored. |
| `offline_notice` | No | Post one "module is offline" line (once per channel per 10 minutes) when a message for a silent module arrives. Default: `false`. |
| `max_age_s` | No | Router: messages older than this are skipped after downtime (minimum 60). Default: `600`. |
| `live_modules` | No | Bus modules whose Discord writes are real (also needs `dry_run` off). Others get result code `dry` |
| `extra_channels` | No | Router: more channel keys for bus modules: `key`, `id` (a number) |
| `categories` | No | Category keys for channel administration, for example `wip=<id>,done=<id>`. |
| `permission_templates` | No | Named channel permission templates a module may ask for. |
| `modules` | No | Bus modules and what they are GRANTED: `name`, `signing_key` (secret, one per module), `channels` (channel keys), `ops`, `rate_per_min`, `public_words` |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `dry_run` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).
- **No messages are read.** Check `discord_token`, that the bot can see the channels, and that `channels` maps a key to each channel id.

## Privacy

Messages in the channels you configure are read through Discord and replies go back to Discord. Everything else (bus messages, journal) stays on your network. The bot token is stored only in the add-on options.

## More details

### 0.7.0: features moved out
Until 0.6.x this add-on also ran the shopping-list and house-status commands in-process. They now live in their own add-ons (`ih_discord_shopping`, `ih_discord_house`). The old options of those features (`live_features`, `retired_features`, `old_service_disabled`, `shopping_list_entity`, `meal_plan_script`, `shop_names`, `aisles`, `aisle_order`, `command_event`, `presence`, `house_entities`, `status_device`, `aliases`, `brain_webhook_id`, `brain_url`, `channels.brain_inbox`) and the switches `router_enabled` / `enable_bus` are still **accepted** so saved options keep validating after the update, but they are **ignored** (shown in `/status` as `deprecated_options_ignored`). The router and the bus are always on. Move those settings to the module add-ons. The brain-inbox plugin was removed.

### Going live
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
| `live_modules` | Bus modules whose Discord writes are real (also needs `dry_run` off). Others get result code `dry` | `["ih_discord_house","ih_discord_shopping"]` |
| `offline_notice` | Post one "module is offline" line (once per channel per 10 minutes) when a message for a silent module arrives | `false` |
| `log_payloads` | false = each handled Discord message or reaction logs ONE line with metadata only (id, source, what, chars in/out, ms, ok, why). true = the line also holds the first 200 chars of the request and the reply (one line, newlines escaped). Keys and tokens are never logged. | `false` |

### Router and bus (always on since 0.7.0)
Modules never get the token or a channel number, only channel **keys**. The channel keys are `house` (`channels.house`), `shopping` (`channels.shopping`) and the `key`s in `extra_channels`.
- **Router.** The add-on keeps a cursor per channel (`/data/cursor.json`) and a journal (`/data/journal.jsonl`, 24 hours). A restart resumes from the cursor: nothing is answered twice. The first run starts at the newest message. Messages older than `max_age_s` are skipped. Create `/data/CURSOR_TO_NOW` before a start to jump every cursor to the newest message (used when handing a channel over).
- **Claims.** A module claims a channel key with `exact` words, a `prefix`, a `regex` or `any`. All modules with a matching specific claim get the message. If none match, the single catch-all gets it. A second catch-all on one channel is refused. Only users in `allowed_user_ids` reach a module, except for its `public_words`.
- **Grants.** What a module may do is set here, in `modules`, not by the module. A claim or op outside the grant gets `denied_channel` / `denied_op`. Default ops: post, reply, react, unreact, edit, delete, typing, watch, unwatch. A module can edit, delete or watch only messages it created.
- **Signing.** Every bus message carries `v`, `ts` and an HMAC-SHA256 signature made with that module's own key over the whole message. Wrong key, changed content, another module's topic, older than 120 s, or a repeated `op_id` are refused and counted (`/status`, never the content). Keys are never logged. The topics, fields and the client for module authors are in `bus_client.py`.
- **Dry-run.** A bus module writes to Discord only when `dry_run` is off AND it is in `live_modules` AND neither `/data/STOP_LIVE` nor `/data/STOP_LIVE_<module>` exists. `/status` shows the router, every module's last heartbeat and the rejected counts.
- **Shadow module (S2).** A `modules` row with `"shadow": true` gets COPIES of what the live claimant gets, may claim the same channel key as the live catch-all module, and every op it sends is forced dry (result code `dry`). Use it to try a new module beside the live one and compare answers. Example row for the house module: `{"name":"ih_discord_house","signing_key":"<secret>","channels":["house"],"ops":["post","typing"],"rate_per_min":30,"public_words":["whoami"],"shadow":true}`. The channel key `house` is the key of `channels.house`. Cut-over: remove `shadow` (or set it false), add the module to `live_modules`, stop the old module first.
- **Channel administration (0.8.0, all OFF by default).** A module may ask the transport to create, move, re-topic and delete channels (ops `create_category`, `create_channel`, `move_channel`, `set_topic`, `delete_channel`, `list_channels`; code in `admin.py`). The module names only keys; the transport decides. Options: `categories` (`wip=<category id>,done=<category id>`: category keys), `permission_templates` (`private_owner_and_bot=<user id>+<user id>`: a template means "visible to these users and the bot only"; a module can never send raw permissions). Per `modules` row (all optional): `can_create_channels` (default false), `allowed_parent_categories` (comma list of category keys), `name_pattern` (regex the lower-cased name must fully match, default `[a-z0-9-]{1,40}`), `max_channels` (default 20), `max_creates_per_hour` (default 5), `permission_template` (the one template this module may ask for; none = it cannot create), `can_delete` (default false; only channels in `/data/created_channels.json` owned by that module, plus empty categories it created when `category_ops`), `category_ops` (default false). New channels get the key `<module>.<name>`, which is added to that module's channels automatically and saved in `/data/created_channels.json`. Limits: 3 quota hits in an hour write `/data/ADMIN_LOCK_<module>` (delete the file to re-enable); `/data/STOP_ADMIN` and `/data/STOP_ADMIN_<module>` stop only the admin ops; in dry-run (module dry, shadow, not live) nothing is created and the answer is code `dry`; Discord's 50-per-category and 500-per-server limits are checked first. `/data/audit.jsonl` has time, module, op, channel key, id and result code (never text or topics). At start and every 6 hours the transport compares its state file with Discord: missing channels are marked dead (the owner gets one `channel_gone` event on the key `_admin`), unknown channels in managed categories that match a name pattern are listed on `/status` as orphans.
- **Test module.** `tests/ih_echo.py` is a fake module that echoes; the tests run it over an in-memory bus (`bus_client.FakeBus`).
