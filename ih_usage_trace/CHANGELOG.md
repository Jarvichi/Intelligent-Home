# Changelog

## 0.2.1
- Update the shared Intelligent Home visual theme and components.

## 0.2.0
- Live countdown tile at the top of the page: "time until under pace" in days, hours, minutes and seconds, ticking every second in the browser from an absolute target time (no polling per second; it re-syncs from the server every minute). States: counting down, "Under pace now", "No data" (newest sample older than 15 minutes, or no pace number).
- `GET /countdown` (API and page): `state`, `pace`, `hours_until_under`, `target_ts`/`target_iso`, `sample_ts`/`sample_iso`, `age_s`, `server_ts`. Same maths as the Home Assistant sensor `sensor.claude_hours_until_under_pace`: hours = max(pace, 0) / (100 / 168).

## 0.1.0
- First version. Reads the Claude session and week usage sensors (and the week pace) every 5 minutes into its own database. `POST /events` (token) stores labelled events (source, label, start, optional end and ref; never message text); `GET /events` and `GET /series` read them back. Retention (default 60 days). `mode`: `shadow` (default) records the series but only logs "would store" for events; `live` stores them.
- Page: line chart of session % and week %, an event lane per source with hover labels, source and period filters (search, selects, Clear), a table of events, and an "unattributed jump" flag for any rise of `jump_threshold` points or more (default 2) in one sample step with no overlapping event.
