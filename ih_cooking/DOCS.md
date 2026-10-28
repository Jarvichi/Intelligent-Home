# Intelligent Home - Cooking

## What it does

Data owner for recipes and the meal plan, plus the meal-vs-calendar check (read-only in v0.1). Stand-alone family member.

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
| `ha_url` | No | Home Assistant (default `http://supervisor/core`) and a long-lived token. Empty token = no calendar data. |
| `ha_token` | No | Home Assistant (default `http://supervisor/core`) and a long-lived token. Empty token = no calendar data. |
| `plan_entity_prefix` | No | Optional, see above. |
| `rate_per_min` | No | Requests per minute (default 60). |
| `clash_config` | No | JSON text with the house-specific settings (below). Empty = example defaults (Person A/B/C). |

## Troubleshooting

- **Calendar check is empty.** Set `ha_token` (a long-lived access token) so the add-on can read your calendars.

## Privacy

Nothing leaves your network. Calendars are read from your own Home Assistant.

## More details

### API (all but `/healthz` need `Authorization: Bearer <api_token>`)
| Path | Returns |
|---|---|
| `GET /healthz` | `ok` (no token) |
| `GET /status` | version, counts, whether tokens are set (never the tokens) |
| `GET /recipes` | list: slug, title, serves, meal_time, oven |
| `GET /recipes/<slug>` | one recipe (JSON as stored) |
| `GET /plan` | `{"plan": {"mon": "...", ... "sun": "..."}, "source": "file"\|"ha"\|"empty"}` |
| `GET /clashes?days=7&today=YYYY-MM-DD` | one entry per day: meal, kind (lunch/evening), window, `away` (person -> reasons), `home`, `eating`, **`all_out`**, `unassigned`, `notes`, `clash`, `advice`; plus `clashes` (only days with a clash) and `all_clear` |

`all_out: true` means nobody is home for that meal (`home` is empty, `advice` says so). Rate limit: `rate_per_min` (429). Kill switch: file `/data/STOP` (503). Tokens, URLs and calendar text are never logged.

### Data
- Recipes: `app/seed_recipes/*.json` are copied to `/data/recipes/` on first start only; later changes in `/data` are never overwritten. The seed copies have personal names removed.
- Meal plan: `/data/mealplan.json` (`{"mon": "Meal", ...}`), else, if `plan_entity_prefix` is set (for example `input_select.meal_`), the seven entities `<prefix>mon` ... `<prefix>sun` are read from Home Assistant (GET only), else empty. No plan write support in v0.1.
- Calendars: Home Assistant REST `GET /api/calendars/<entity>?start=&end=` using `ha_url` and `ha_token`.
