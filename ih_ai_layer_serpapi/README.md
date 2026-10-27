# Intelligent Home - AI Layer - SerpApi Agent (ih_ai_layer_serpapi)

An agent for **Intelligent Home - AI Layer** (`ih_ai_layer`). It registers with the parent with the capabilities `showtimes` and `web_search`, and answers the parent's signed `POST /ask` using [SerpApi](https://serpapi.com) Google results. **Discord is NOT part of this**; a separate Discord AI bot add-on comes later.

## Options (examples fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `serpapi_key` | Your own SerpApi key. Empty = every question gets "not configured". Never logged or shown in `/status`. |
| `register_key` | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` | The parent (default: Supervisor gateway, port 8801). |
| `callback_url` | How the parent reaches this agent (empty = gateway, port 8802). |
| `daily_cap` | Max real SerpApi searches a day (default 5; the free plan is small). Counted before the call. 0 = never search. |
| `cache_hours` | Same question inside this time is answered from cache and costs nothing (default 6). |
| `default_cinema` | Used for showtimes when the request context has no `cinema`. |

For showtimes the request `context` may hold `cinema` and `film`.

## IMPORTANT: the parser is a guess
The real SerpApi response layout for UK cinema showtimes was **not checked**. `app/showtimes_parse.py` assumes `showtimes -> theaters -> showing -> time` (from an older prototype) and `organic_results` with `title`, `snippet`, `link`. The tests use **sample JSON written by hand**, not a real response. If the layout differs, the answer says "No showtimes found ... layout may differ". Fix the parser (one file) after one real response has been looked at.

## Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap, cache hits and whether the key is set (not the key). No request text is logged. Request text is sent to SerpApi (search words only).

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual. Version 0.1.0: code and tests only; not installed, not build-tested.
