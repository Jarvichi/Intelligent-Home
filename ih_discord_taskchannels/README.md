# Intelligent Home Task channels (`ih_discord_taskchannels`)

One private Discord channel per task, as a **module** of the `ih_discord` transport (0.8.0 or newer).
In the commands channel (channel key `tasks` by default) an allowed user types:

| Command | What happens |
|---|---|
| `new <name> \| <goal>` | Creates `task-<name>` in the "work in progress" category. The goal becomes the channel topic. An optional third part (`\| <opening message>`) is posted first. |
| `done <name> \| <summary>` | Moves the channel to the "done" category and posts the summary. |
| `reopen <name>` | Moves a done channel back. |
| `list` | Shows the tracked task channels and how long until a done one is deleted. |
| `help` | Short help. |

A daily cleanup deletes channels that have been done for more than `cleanup_days` (default 30). It only asks for channels **this module created**;
the transport refuses to delete anything else (channels made by hand, other modules, categories with content).

It does **not** hold a Discord token, a channel number or a Home Assistant token. It talks to the transport over a signed MQTT bus using the
admin operations `create_channel`, `move_channel`, `set_topic`, `delete_channel`, `list_channels` and `post`. The transport decides what is allowed.

## Shadow mode (default)

`shadow: true` (the default): every operation is sent with `dry: true`, nothing is created and nothing is recorded. What it WOULD do is logged as
`[DRY] would create channel ...`. `/status` shows `would_total` and `last_would`. The file `/data/STOP_LIVE` forces the same at once.
The transport can also stop only the admin operations with `/data/STOP_ADMIN` (all modules) or `/data/STOP_ADMIN_<module>`.

## Setup (needs the transport options first; the transport grants are the real switch)

1. In the `ih_discord` options set `categories` (for example `wip=<category id>,done=<category id>`), `permission_templates`
   (for example `private_owner_and_bot=<user id>`), an `extra_channels` row for the commands channel key `tasks`, and a `modules` row for this module:
   `{"name":"ih_discord_taskchannels","signing_key":"<secret>","channels":["tasks"],"ops":["post"],"rate_per_min":30,"shadow":true,
   "can_create_channels":true,"allowed_parent_categories":"wip,done","name_pattern":"task-[a-z0-9-]{1,40}","max_channels":20,"max_creates_per_hour":5,
   "permission_template":"private_owner_and_bot","can_delete":true}`. Without these flags every admin operation is refused.
2. Create an MQTT user for this module; put the same signing key here and in the transport row.
3. Fill in the options below. `options.example.json` has fake values.
4. Start the add-on. Check `http://<host>:8797/status`: `bus.connected` true, `bus.last_heartbeat_s` small. Leave `shadow` on until the dry log looks right.

## Options

| Option | Meaning | Default |
|---|---|---|
| `module_name` | Name on the bus. Must match the transport row | `ih_discord_taskchannels` |
| `signing_key` | Secret shared with the transport row | empty (inactive) |
| `shadow` | Log only, create nothing | `true` |
| `command_channel_key` | Channel KEY where commands are typed | `tasks` |
| `wip_category`, `done_category` | Category KEYS (defined in the transport option `categories`) | `wip`, `done` |
| `template` | Permission template name to ask for. Empty = the one granted to this module | empty |
| `cleanup_days` | Delete done channels after this many days | `30` |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | The broker | `core-mosquitto`, `1883`, `ih_discord_taskchannels`, empty |
| `ingest_url`, `ingest_token` | Orchestrator status push (optional) | empty token = no push |

## Behaviour

- Only users on the transport's allow-list reach this module. A command is acknowledged before it is acted on.
- Channel names are `task-` plus up to 30 letters, digits or hyphens. The channel key is `<module name>.<name>`.
- The goal text is never stored in this add-on (only names, dates and a short summary in `/data/state.json`); it lives in the channel topic.
- If a channel is deleted by hand, the transport tells this module once and the task is forgotten.
- Channels created by an older tool are not owned by this module and are never deleted by it.

## Endpoints

`GET /healthz` (200 ok, 503 when not healthy) and `GET /status` (no secrets). Port 8797.
