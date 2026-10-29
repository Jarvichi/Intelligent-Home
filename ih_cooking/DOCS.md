# Intelligent Home - Cooking

## What it does

Data owner for recipes and the meal plan, plus the meal-vs-calendar check (dry cook control, recipes page). Stand-alone family member.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Cooking**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `api_token`.
4. Start the add-on and check `/healthz` and the log.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `api_token` | Yes | Bearer token callers must send. Empty = everything refused. |
| `ha_url` | No | Home Assistant (default `http://supervisor/core`) and a token (calendars, states, services). |
| `ha_token` | No | Home Assistant (default `http://supervisor/core`) and a token (calendars, states, services). |
| `plan_entity_prefix` | No | Optional, see above. |
| `rate_per_min` | No | Requests per minute (default 60). |
| `clash_config` | No | JSON text with the house-specific calendar settings (below). |
| `cook_mode` | No | `off` (cooks refused), `dry` (default; logs only), `live` (acts, if the guard allows). |
| `cook_confirm` | No | Must be `I-AM-THE-ONLY-COOK-CONTROLLER` for live control. |
| `other_controller_entity` | No | Entity the old cook daemon updates while alive (its `last_updated` is the heartbeat). |
| `other_controller_max_age_s` | No | Heartbeat older than this counts as silent (default 120). |
| `other_controller_none` | No | True = there is no other controller (needed for live if no heartbeat entity). Default: `false`. |
| `discord_token` | No | Bot token used to post cooking messages. Empty = Discord messages are dropped (logged). |
| `discord_channel_id` | No | Channel for step messages. |
| `discord_child_id` | No | User id that gets the "dinner in 15 minutes" message and nudges. |
| `discord_owner_id` | No | User id that gets the oven alerts. |
| `child_name` | No | Name used in the child's messages. |
| `player_entity` | No | Speaker used for voice (volume is set to 50% and put back). Empty = no voice. |
| `player_group` | No | Comma list of speakers whose music is paused while speaking and resumed after. |
| `tts_entity` | No | Text-to-speech entity (default `tts.google_translate_en_com`). |
| `oven_prefix` | No | Common start of the oven's entity ids, for example `my_oven_` (the add-on adds `cooking_oven_status_currentcavitytemperature`, `bsh_common_status_operationstate`, ...). |
| `oven_device_id` | No | Home Assistant device id of the oven, for the program services. |
| `plan_sensor_entity` | No | Sensor the wall display cooking screen reads (default `sensor.cooking_plan`). |
| `rerender_script_entity` | No | Script run when a cook ends so the wall display redraws. Optional. |
| `publish_webhook_url` | No | Where `/publish` (and every recipe save) sends `{files: {path: text}}`: `brain/...` notes and `www/recipes/...` copies. Empty = the www copy is skipped quietly. |
| `publish_live` | No | False (default) = publishing only logs counts. |
| `brain_url` | No | Brain add-on MCP address, e.g. `http://HOST:8820/mcp`. Recipe notes are written with `brain_write`; only changed notes are sent (hashes kept in /data). |
| `brain_token` | No | Bearer token of the Brain add-on (needs its read_only off). |
| `facts_url` | No | Orchestrator API address (for example `http://<host>:8790`). With `facts_key`, the cook engine writes today's eat time as fact `cooking/eat_time` (`{"date", "eat"}`, expires at local midnight; cleared when the cook stops). Empty = off. A failure never blocks cooking. |
| `facts_key` | No | This add-on's `fact_clients` key (module `cooking`, write true) in the orchestrator. |

## Troubleshooting

- **Calendar check is empty.** Set `ha_token` (a long-lived access token) so the add-on can read your calendars.
- **Cooking does nothing real.** `cook_mode` is `dry` by default: it only logs what it would do. See `GET /cook` for the reason live control is refused (`guard`).

## Privacy

Calendars, the oven, the speaker and the cooking sensor are your own Home Assistant. Only if you set `discord_token` do cooking messages go out, through Discord. Recipes publish only to the webhook you set.

## More details

### API (all but `/healthz` need `Authorization: Bearer <api_token>`)
| Path | What it does |
|---|---|
| `GET /healthz` | `ok` (no token) |
| `GET /status` | version, counts, whether tokens are set (never the tokens), plus `cook` (mode, live, guard reason, running) |
| `GET /recipes`, `GET /recipes/<slug>` | list, one recipe |
| `PUT /recipes/<slug>` | validated edit of an existing recipe; the old file is kept in `/data/recipe_backups/` |
| `POST /recipes` | new recipe from `{name, steps:[{before,text,short}], prep, shopping, equipment}` |
| `GET /plan`, `PUT /plan` | meal plan `{mon..sun}`; PUT writes `/data/mealplan.json` |
| `POST /shop` | `{recipes:[slugs], groups:[...]}` -> merged shopping list, updates the meal-items ledger |
| `GET /clashes?days=7&today=YYYY-MM-DD` | who is away for each planned meal (see below) |
| `POST /cook` | `{recipe, eat:"HH:MM"}` starts (replaces) a cook; `POST /cook/stop`; `GET /cook` status |
| `POST /cook/done`, `/cook/ack`, `/cook/tick`, `/cook/delay` | the wall display buttons: `{row, did}`, `{row}`, `{row}`, `{minutes, row}` |
| `POST /oven/start`, `/oven/set`, `/oven/stop` | `{temp, program, minutes}` ; retried until the oven confirms. Dry = logs `would set oven ...` |
| `POST /publish` | writes changed recipe notes to the Brain add-on (`brain_url`) and sends www copies to `publish_webhook_url` if set (dry: logs counts only) |
| `POST /import` | seed `/data` from the host (see Seeding). Never overwrites unless `overwrite: true` |

Rate limit: `rate_per_min` (429). Kill switch: file `/data/STOP` (503). Tokens, URLs and calendar text are never logged.

### The recipes page
Open the add-on's **Open Web UI** (ingress; no sidebar panel). It lists, searches, edits and starts a cook. The page cannot force live control: starting a cook follows `cook_mode`. Ingress is only reachable from the Supervisor.

### Data and seeding
- Everything lives in `/data`: `recipes/*.json`, `recipe_backups/`, `mealplan.json`, `staples.json`, `meal_items.json`, and `cook/` (plan, state, log).
- The image holds 22 example recipes (personal names removed) that are copied to `/data/recipes/` on first start only.
- **To seed from your own files** run on the machine that has them: `IHC_TOKEN=<api_token> python3 seed_from_host.py http://<addon-host>:8809 <cooking folder>` (add `--dry` first). It reads `recipes/*.json`, `staples.json`, `meal_items.json` and posts them to `/import`. Existing files are kept unless `--overwrite`.
- The meal plan comes from `/data/mealplan.json`, else (if `plan_entity_prefix` is set, for example `input_select.meal_`) from seven Home Assistant entities, else empty.
- After a restart a running cook does **not** resume by itself. Start it again from the page.

### Cook engine
Same behaviour as the old host scripts: the plan is built from the recipe (steps are minutes before serving, plus a "get everything out" gate that slides the plan later until done is tapped), checked every 5 s. Each due step is posted to Discord and spoken (music on the speaker group is paused and always resumed). Urgent steps are repeated once after 2.5 minutes unless acknowledged. A message goes to the child 15 minutes before serving. The wall display cooking sensor is updated every pass. A recipe step may carry `"oven": {"action": "start"|"set"|"stop", "temp": 200}` to control the oven when it is announced.

**Oven control** re-sends the command until the oven reports it is running (start), the cavity temperature moves (set) or it is no longer running (stop). The oven refuses commands for about a minute after anyone touches it, so a refusal is simply retried. After more than a minute without success the speaker says so and the owner gets a Discord message; if it never works it asks for the oven to be set by hand.

#### The guard: never two live controllers
Live control is allowed only when **all** of these hold, checked when a cook starts and again every 15 seconds while it runs:
1. `cook_mode` is `live` and `cook_confirm` is exactly `I-AM-THE-ONLY-COOK-CONTROLLER`.
2. No file `/data/cook/STOP_LIVE`.
3. Home Assistant is reachable (`ha_token`, or the add-on's own access when it is empty).
4. The **other controller** is declared. Either `other_controller_entity` names an entity that the old daemon updates while it is alive, and its `last_updated` is **older** than `other_controller_max_age_s`; or `other_controller_none` is true (you confirm the old daemon is gone).
5. `other_controller_entity` is not this add-on's own `plan_sensor_entity`.

If anything cannot be read or is in doubt, live control is refused (fail closed). If the guard fails while a cook is running, the cook carries on in dry mode and the log says `GUARD: ...`. `GET /cook` shows the reason.
