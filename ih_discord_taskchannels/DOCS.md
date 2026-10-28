# Intelligent Home - Discord - Task channels

## What it does

Task channels for Discord as a bus module of ih_discord (one private channel per task, Work in Progress and Done categories, goal as topic, daily cleanup of old done channels). Shadow mode by default: creates nothing. Module of the `ih_discord` transport.

## Requires

- `ih_discord` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Discord - Task channels**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `signing_key`, `mqtt_password`, `ingest_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `module_name` | No | Name on the bus. Must match the transport row. Default: `ih_discord_taskchannels`. |
| `signing_key` | Yes | Secret shared with the transport row |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `command_channel_key` | No | Channel KEY where commands are typed. Default: `tasks`. |
| `wip_category` | No | Category KEYS (defined in the transport option `categories`). Default: `wip`. |
| `done_category` | No | Category KEYS (defined in the transport option `categories`). Default: `done`. |
| `template` | No | Permission template name to ask for. Empty = the one granted to this module |
| `cleanup_days` | No | Delete done channels after this many days. Default: `30`. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_discord_taskchannels`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `ingest_url` | No | Orchestrator status push (optional). Default: `http://172.30.32.1:8790`. |
| `ingest_token` | Yes | Orchestrator status push (optional) |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

Task names become Discord channel names and are visible to the users allowed on those channels. Nothing else leaves your network.

## More details

### Shadow mode (default)

`shadow: true` (the default): every operation is sent with `dry: true`, nothing is created and nothing is recorded. What it WOULD do is logged as
`[DRY] would create channel ...`. `/status` shows `would_total` and `last_would`. The file `/data/STOP_LIVE` forces the same at once.
The transport can also stop only the admin operations with `/data/STOP_ADMIN` (all modules) or `/data/STOP_ADMIN_<module>`.

### Setup (needs the transport options first; the transport grants are the real switch)

1. In the `ih_discord` options set `categories` (for example `wip=<category id>,done=<category id>`), `permission_templates`
   (for example `private_owner_and_bot=<user id>`), an `extra_channels` row for the commands channel key `tasks`, and a `modules` row for this module:
   `{"name":"ih_discord_taskchannels","signing_key":"<secret>","channels":["tasks"],"ops":["post"],"rate_per_min":30,"shadow":true,
   "can_create_channels":true,"allowed_parent_categories":"wip,done","name_pattern":"task-[a-z0-9-]{1,40}","max_channels":20,"max_creates_per_hour":5,
   "permission_template":"private_owner_and_bot","can_delete":true}`. Without these flags every admin operation is refused.
2. Create an MQTT user for this module; put the same signing key here and in the transport row.
3. Fill in the options below. `options.example.json` has fake values.
4. Start the add-on. Check `http://<host>:8797/status`: `bus.connected` true, `bus.last_heartbeat_s` small. Leave `shadow` on until the dry log looks right.

### Behaviour

- Only users on the transport's allow-list reach this module. A command is acknowledged before it is acted on.
- Channel names are `task-` plus up to 30 letters, digits or hyphens. The channel key is `<module name>.<name>`.
- The goal text is never stored in this add-on (only names, dates and a short summary in `/data/state.json`); it lives in the channel topic.
- If a channel is deleted by hand, the transport tells this module once and the task is forgotten.
- Channels created by an older tool are not owned by this module and are never deleted by it.

### Endpoints

`GET /healthz` (200 ok, 503 when not healthy) and `GET /status` (no secrets). Port 8797.
