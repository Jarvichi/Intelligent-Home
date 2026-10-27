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

## Parser (layout checked once, 27 Oct 2026)
Showtimes layout was checked against one real response: `showtimes -> [day, movies -> [name, showing -> [time, type]]]`. The cinema is the one the search names (default_cinema or context `cinema`); context `film` filters films. `organic_results` is still unchecked.

## Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap, cache hits and whether the key is set (not the key). No request text is logged. Request text is sent to SerpApi (search words only).

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual. Version 0.1.2: showtimes parser checked against one real response.
