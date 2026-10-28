# Intelligent Home - Jobs - Met Office

## What it does

Child of Intelligent Home - Jobs: Met Office forecast sensors. Dry-run by default. Child of `ih_jobs`.

## Requires

- `ih_jobs` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Jobs - Met Office**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `register_key`, `met_office_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `old_loops_disabled` | No | You switched older scripts off. Default: `false`. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8792`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `register_key` | Yes | Shared secret with the parent (secret) |
| `own_timer_min` | No | Run by itself every N minutes (0 = only when the parent or start-up asks). Default: `0`. |
| `met_office_key` | Yes | Met Office DataHub API key (secret) |
| `timezone` | No | Day boundaries (default `Europe/London`) |
| `zone_entity` | No | Zone whose latitude/longitude is used. Default: `zone.home`. |
| `fallback_weather_entity` | No | Weather entity used when Met Office data is missing or stale. Default: `weather.forecast_home`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.

## Privacy

The forecast request to the Met Office includes the location of your home zone and your own API key. Nothing else leaves your network.

## More details

### How it joins the parent
Give this add-on and the parent the **same** `register_key` (any long random text). Every 30 seconds the child posts a signed registration to the parent (`parent_url`). The parent lists it (`GET /children`) and forwards scheduled runs to it. The child keeps working if the parent is stopped (it runs once 90 s after start, and every `own_timer_min` minutes if above 0). With no `register_key` the link is off. The API is never called more than once per 55 minutes (shared cache in `/data`).

### Going live
Acts only when `mode` is `live`, `old_loops_disabled` is on and `/data/STOP_LIVE` does not exist.
