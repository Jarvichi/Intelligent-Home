# Intelligent Home - Lifestyle - Groceries

## What it does
Keeps the whole of every shopping receipt in its own SQLite database and answers: which products changed price, why one week cost more than another, and which products are due again from how often they are bought.

## Options
- `mode`: `dry` (default) only checks what is sent and writes nothing; `live` stores receipts.
- `api_token`: needed to send receipts or aliases. Empty means sending is refused. Reading statistics needs no token.

## API (other add-ons only; port 8801 is not published)
`GET /healthz`, `GET /stats/due`, `/stats/prices`, `/stats/weeks`, `/stats/week_diff?a=YYYY-MM-DD&b=YYYY-MM-DD`, `POST /receipts`, `POST /alias`.

## Privacy
No card or payment details are accepted or stored. Purchases never leave the home network.
