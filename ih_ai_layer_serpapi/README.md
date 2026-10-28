# Intelligent Home - AI Layer - SerpApi Agent (ih_ai_layer_serpapi)

An agent for **Intelligent Home - AI Layer** (`ih_ai_layer`). It registers with the parent with the capabilities `showtimes` and `web_search`, and answers the parent's signed `POST /ask` using [SerpApi](https://serpapi.com) Google results. **Discord is NOT part of this**; a separate Discord AI bot add-on comes later.

## Options (examples fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `serpapi_key` | Your own SerpApi key. Empty = every question gets "not configured". Never logged or shown in `/status`. |
| `register_key` | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` | The parent (default: Supervisor gateway, port 8801). |
| `callback_url` | How the parent reaches this agent (empty = gateway, port 8802). |
| `spend_cap` | Max PAID calls a day. Default 0 = free only: paid calls are refused with error `spend_cap`. Counted before the call. |
| `free_tier` | true = your plan costs nothing per call, so calls are free (spend_cap not needed). Default false. The agent cannot see real billing, so it treats every call as paid unless this is true (or the model name ends in `:free`). Stops by itself when the upstream credits run out (upstream error). |
| `daily_cap` | Max real SerpApi searches a day (default 5; the free plan is small). Counted before the call. 0 = never search. |
| `cache_hours` | Same question inside this time is answered from cache and costs nothing (default 6). |
| `default_cinema` | Used for showtimes when the request context has no `cinema`. |
| `log_payloads` | false (default) = each /ask logs ONE line with metadata only (id, caller, capability, model, chars in/out, ms, ok, why, free, shadow). true = the line also holds the first 200 chars of the request text and of the answer (one line, newlines escaped). Keys, tokens and headers are never logged. |

For showtimes the request `context` may hold `cinema` and `film`.

## Parser (layout checked once, 27 Oct 2026)
Showtimes layout was checked against one real response: `showtimes -> [day, movies -> [name, showing -> [time, type]]]`. The cinema is the one the search names (default_cinema or context `cinema`); context `film` filters films. `organic_results` is still unchecked.

## Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap, cache hits and whether the key is set (not the key). Request text is logged only when `log_payloads` is true (200 chars); by default the log has metadata only. Request text is sent to SerpApi (search words only).

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual. Version 0.1.2: showtimes parser checked against one real response.
