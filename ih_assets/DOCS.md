# Intelligent Home - Assets

## What it does

Keeps a list of things you own: games, DVDs, toys, devices, books and other items. Each item has a quantity, an optional cost, and a low and a high resale value (per item). The page shows the total low and high value, how many items are not priced yet, and a bar chart of the low and high total by day. A point is added once a day (while the add-on runs) and whenever you add, price, sell or delete an item. Marking an item sold takes it out of the totals, so the chart steps down.

Values are for planning only. Items without both a low and a high value count as "not priced" and are left out of the totals.

## Page

Open **IH · Assets** in the sidebar (administrators only). Search and filter by category or priced state, sort by any column, add an item, set a price, mark an item sold (press twice to confirm), or look up a barcode. The barcode box sends only the number to Open Library (ISBNs) or UPCitemdb; an item added from a barcode has no price until you set one.

## Options

| Option | Meaning |
|---|---|
| `mode` | `dry` (default) publishes nothing. `live` publishes `sensor.assets_low` and `sensor.assets_high` (state in GBP, with count, unpriced and by-category attributes) each interval. |
| `api_token` | Bearer token for the API on port 8852. Empty = the API refuses everything except `/healthz`. |
| `interval_min` | Minutes between history and sensor updates (5 to 1440, default 60). |

## Install flags (IH runbook)

Boot: auto. Watchdog: `/healthz` on port 8852. Turn on `auto_update` in the Supervisor after the first start. Add-on data: `/data/ih.db` with a daily snapshot in `/data/backup`. No host folders are mapped.

## First-time import (optional)

To bring in an older list, put `inventory.json` (and `history.csv`) in the add-on's `/data/import/` folder before the first start. Each item needs `name`, optional `category`, `platform`, `qty`, `cost`, `low_gbp`, `high_gbp`, `bought_date`, `sold_date`, `source`, `checked`; `history.csv` has `date,low,high,count`. The import is refused (and logged) if the database already has data.

## Switching on the sensors

If other sensors already use `sensor.assets_low` or `sensor.assets_high`, remove them first, then set `mode: live`. Two writers to the same entity overwrite each other. This is a Home Assistant configuration change: back up first.

## API

See the top of `app/main.py`. Bearer `api_token`: `GET /summary`, `/items`, `/items/<id>/prices`, `/history`, `/status`; `POST /items`, `POST /items/<id>/sell`, `POST /barcode {code, add, category}`; `PATCH` and `DELETE /items/<id>`. Item names are never logged.

## Troubleshooting

- Page says Unavailable: check the add-on log for `tick failed` or a schema message.
- Sensors do not appear: `mode` is `dry`, or the add-on has no Home Assistant API access.
- Import skipped: the database already has items or history.

## Rollback

Set `mode` back to `dry`, or stop and uninstall the add-on. The data lives only in the add-on's database, so uninstalling deletes it.
