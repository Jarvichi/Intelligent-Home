# Intelligent Home - AI Layer - SerpApi Agent

## What it does

Agent of Intelligent Home - AI Layer: showtimes and web search via SerpApi. Agent (child) of `ih_ai_layer`.

## Requires

- `ih_ai_layer` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - AI Layer - SerpApi Agent**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `serpapi_key`, `register_key` (skip any the settings hub fills).
4. Start the add-on and check `/healthz` and the log.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `serpapi_key` | Yes | Your own SerpApi key. Empty = every question gets "not configured". Never logged or shown in `/status`. |
| `register_key` | Yes | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8801`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `spend_cap` | No | Max PAID calls a day. Default 0 = free only: paid calls are refused with error `spend_cap`. Counted before the call. |
| `free_tier` | No | true = your plan costs nothing per call, so calls are free (spend_cap not needed). Default: `false`. |
| `daily_cap` | No | Max real SerpApi searches a day (default 5; the free plan is small). Counted before the call. 0 = never search. |
| `cache_hours` | No | Same question inside this time is answered from cache and costs nothing (default 6). |
| `default_cinema` | No | Used for showtimes when the request context has no `cinema`. |
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |

## Troubleshooting

- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.
- **Error `spend_cap` or `daily_cap`.** Calls are counted before they are made. Raise the cap, or set `free_tier` to true if your plan is free. A missing or wrong API key also gives an upstream error: check `/status`, which shows whether a key is set (never the key).

## Privacy

Your search text and cinema name are sent to SerpApi using your own key. Results are cached locally. Do not put personal details in questions.

## More details

### Parser (layout checked once, 27 Oct 2026)
Showtimes layout was checked against one real response: `showtimes -> [day, movies -> [name, showing -> [time, type]]]`. The cinema is the one the search names (default_cinema or context `cinema`); context `film` filters films. `organic_results` is still unchecked.

### Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap, cache hits and whether the key is set (not the key). Request text is logged only when `log_payloads` is true (200 chars); by default the log has metadata only. Request text is sent to SerpApi (search words only).
