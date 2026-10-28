# Intelligent Home - Cooking (ih_cooking)

Data owner for **recipes** and the **meal plan**, plus the **meal-vs-calendar check**: for each planned meal, who is not home. Version 0.1.0 is **read-only**: no oven control, no writes to Home Assistant, no Discord. Maps no folders. Port 8809.

## API (all but `/healthz` need `Authorization: Bearer <api_token>`)
| Path | Returns |
|---|---|
| `GET /healthz` | `ok` (no token) |
| `GET /status` | version, counts, whether tokens are set (never the tokens) |
| `GET /recipes` | list: slug, title, serves, meal_time, oven |
| `GET /recipes/<slug>` | one recipe (JSON as stored) |
| `GET /plan` | `{"plan": {"mon": "...", ... "sun": "..."}, "source": "file"\|"ha"\|"empty"}` |
| `GET /clashes?days=7&today=YYYY-MM-DD` | one entry per day: meal, kind (lunch/evening), window, `away` (person -> reasons), `home`, `eating`, **`all_out`**, `unassigned`, `notes`, `clash`, `advice`; plus `clashes` (only days with a clash) and `all_clear` |

`all_out: true` means nobody is home for that meal (`home` is empty, `advice` says so). Rate limit: `rate_per_min` (429). Kill switch: file `/data/STOP` (503). Tokens, URLs and calendar text are never logged.

## Data
- Recipes: `app/seed_recipes/*.json` are copied to `/data/recipes/` on first start only; later changes in `/data` are never overwritten. The seed copies have personal names removed.
- Meal plan: `/data/mealplan.json` (`{"mon": "Meal", ...}`), else, if `plan_entity_prefix` is set (for example `input_select.meal_`), the seven entities `<prefix>mon` ... `<prefix>sun` are read from Home Assistant (GET only), else empty. No plan write support in v0.1.
- Calendars: Home Assistant REST `GET /api/calendars/<entity>?start=&end=` using `ha_url` and `ha_token`.

## Options (examples fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `api_token` | Bearer token callers must send. Empty = everything refused. |
| `ha_url` / `ha_token` | Home Assistant (default `http://supervisor/core`) and a long-lived token. Empty token = no calendar data. |
| `plan_entity_prefix` | Optional, see above. |
| `rate_per_min` | Requests per minute (default 60). |
| `clash_config` | JSON text with the house-specific settings (below). Empty = example defaults (Person A/B/C). |
| `log_payloads` | false = each authenticated API request (not /healthz) logs ONE line with metadata only (request id and caller if the client sends X-Request-Id / X-Caller, path, HTTP status, chars out, ms, ok, why). true = the line also holds the first 200 chars of the request path+query and of the response (one line, newlines escaped). Tokens and calendar text are never logged otherwise. |

### clash_config keys
`timezone`; `people`: list of `{name, calendar, pattern, schedule, travel_minutes, closures}` (`calendar` = that person's own calendar; `pattern` = regex matched against shared-calendar event text; `schedule` = weekday number (0 = Monday) -> list of `[start, end]` such as lessons or shifts; `travel_minutes` each side; `closures` = `{first, last, label, sure}` dates when the schedule does not apply); `shared_calendars`; `away_words`, `ignore_words` (regexes); `evening_window`, `lunch_window` (`["HH:MM","HH:MM"]`); `lunch_words`. A recipe with `meal_time` (before 15:00 = lunch) overrides the window for that meal. Keys left out keep the example default, so set `people` and `shared_calendars` at least.

## After install
Set boot auto, watchdog on, auto update on. Version 0.1.0: not tested against a real Home Assistant.
