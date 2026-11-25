# Changelog

## 0.1.0
- First version. Reads the Claude session and week usage sensors (and the week pace) every 5 minutes into its own database. `POST /events` (token) stores labelled events (source, label, start, optional end and ref; never message text); `GET /events` and `GET /series` read them back. Retention (default 60 days). `mode`: `shadow` (default) records the series but only logs "would store" for events; `live` stores them.
- Page: line chart of session % and week %, an event lane per source with hover labels, source and period filters (search, selects, Clear), a table of events, and an "unattributed jump" flag for any rise of `jump_threshold` points or more (default 2) in one sample step with no overlapping event.
