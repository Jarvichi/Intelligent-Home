# Intelligent Home Discord (ih_discord)

A small Home Assistant add-on that answers chat commands in Discord:

- a **shopping channel**: type items to add them to a Home Assistant to-do list, `shop` posts the list as a tick-list by aisle, `got milk, bread` ticks things off;
- a **house channel** (optional): `status`, `lights`, `lights off`, `temps`, and, if you set the entities, `bins`, `free`, and "when did <person> go out" answers from a presence switch.

Nothing about your home is in the code. Every channel, entity, person, shop name and aisle comes from the add-on **options** (below). An option left empty means that feature is not offered.
It maps no folders, stores no secrets in code, and starts in **dry-run**: it reads Discord and Home Assistant but only logs what it WOULD post or change.

## Install (local add-on)
Copy this folder to your local add-ons share, reload the add-on store, install, fill in the options, start. The add-on never installs or restarts anything by itself.

## Going live
Dry-run is the default. A feature acts for real only when ALL of these are true: `dry_run` is off, the feature is in `live_features`, `old_service_disabled` is on (you confirm that any older bot is switched off, so two bots do not answer), and the file `/data/STOP_LIVE` does not exist (a kill switch: create it and everything is dry again within seconds). `set_live.py` flips these options in one step (needs `SUPERVISOR_TOKEN`).

## Options
All values below are examples (fake). See `options.example.json`.

| Option | Meaning | Example |
|---|---|---|
| `discord_token` | Bot token (secret) | (your token) |
| `dry_run` | Log only, never post or change | `true` |
| `live_features` | Features that may act when dry_run is off | `["shopping","house"]` |
| `old_service_disabled` | You switched any older bot off | `false` |
| `bot_name` | Name shown in `/status` | `"Discord module"` |
| `channels.shopping` | Shopping channel: a name or a channel number | `"groceries"` |
| `channels.house` | House channel (empty = house commands off) | `"123456"` |
| `channels.brain_inbox` | Notes-inbox channel (optional plugin only) | `""` |
| `allowed_user_ids` | Discord user ids allowed to give commands | `["1111"]` |
| `shopping_list_entity` | Home Assistant to-do list | `"todo.shopping_list"` |
| `meal_plan_script` | Script run before the list is built (empty = none) | `"my_meal_plan"` |
| `shop_names` | Extra shop names for "I'm at ...", `a\|b` | `"corner shop\|acme"` |
| `aisles` | Your aisles: `name` and `words` (a\|b\|c). Empty = built-in generic list | `[{"name":"Fruit","words":"apple\|pear"}]` |
| `aisle_order` | Order the aisles are listed in (empty = as given) | `["Fruit","Tins"]` |
| `command_event` | HA event fired for each accepted house command | `"ih_discord_command"` |
| `presence` | People for who-is-home answers: `name`, `entity` (a switch or binary sensor that is `on` when home) | `[{"name":"Alex","entity":"switch.alex_home"}]` |
| `house_entities` | Optional: `alarm`, `boiler`, `thermostat`, `bin_next`, `bin_days`, `free_power_events`, `free_power_banked` | `{"alarm":"alarm_control_panel.home"}` |
| `status_device` | Optional extra line in `status`: `label`, `battery`, `seen` | `{"label":"Tablet","battery":"sensor.tab_battery","seen":"sensor.tab_seen"}` |
| `aliases` | Extra words for `status`, `bins` or `free` | `[{"command":"status","words":"tablet\|boiler"}]` |
| `ingest_url` | Status page add-on address (default = the Supervisor gateway `172.30.32.1`, port 8790) | `"http://172.30.32.1:8790"` |
| `ingest_token` | Token for the status page (secret) | |
| `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password` | Scheduler bus (default host is the standard Mosquitto add-on) | `core-mosquitto`, `1883` |
| `enable_scheduler`, `signing_key` | Scheduled self-check (off by default; needs a signing key) | `false` |
| `brain_webhook_id`, `brain_url` | Plugin only (see below) | |

## House-specific plugins (NOT part of the generic/public build)
- `features/braininbox.py` and `brain_client.py`: a private notes-server inbox. They are listed in `../public_exclude.txt`; if the files are absent the add-on simply runs shopping + house.
- "catch-up" (running a command on the host) is not available inside the add-on at all (`catchup_cmd` is only used by the stand-alone service).

## Sources
`core.py`, `settings.py`, `features/*.py`, `manifest.json` and `schedule_client.py` are COPIES made by `sync_from_service.sh`. Edit the source, not the copy.

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder: it fails on house-specific terms, private addresses, long ids, emails, phone numbers and secret values.
