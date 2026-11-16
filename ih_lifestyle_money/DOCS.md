# Lifestyle Money

This add-on stores private contract records in `/data/money.db`. Set a unique API token. Its internal port is not published to the LAN. The health endpoint exposes no financial data; all other endpoints require the token.

The authenticated `POST /import` accepts an object with `contracts` (a list) and `sent` (a mapping of legacy reminder keys to dates). The import is atomic and accepted only when the database is empty. Send both the existing records and reminder history together; keep the originals and verify the imported result before any cutover.

`GET /contracts` returns records with calculated lease projections. Authenticated `PUT /contracts` saves a complete contract by ID; it replaces that record and keeps reminder history. `GET /reminders` lists due reminders excluding imported sent keys. Enable `publish_sensor` to publish a separate `sensor.lifestyle_money_contracts` every hour. It defaults off and never overwrites the legacy sensor. No reminder is sent by this version. Keep the existing service running: stopping it would stop reminders and the existing dashboard sensor. Do not operate two reminder senders.

Projected balances assume scheduled payments were made; settlement quotes are dated snapshots. Requests and financial values are not logged. Back up the add-on data before modifying or uninstalling it.
