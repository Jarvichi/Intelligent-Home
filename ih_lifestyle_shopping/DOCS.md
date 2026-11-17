# Lifestyle Shopping

This add-on provides a Home Assistant ingress page for the existing shopping list, with day, shop and added ordering, a store selector, add/tick/undo, quantities, quick-add staples and spending summaries. Store layouts and aisle rules are private and stored in the add-on's `/data/shopping.db`; no household-specific layouts are shipped with the image.

`list_writes_enabled` defaults to false. Read-only browsing and layout setup are available; adding, ticking, changing quantities or applying a meal-shopping preview require deliberately enabling list writes. The add-on uses the configured Home Assistant to-do list so existing UIDs, completed items and sync continue to work. It does not send notifications. Multi-item operations are not atomic: if a call is unconfirmed, reload and review the list instead of retrying blindly.

Set `cooking_url` and `cooking_api_token` to the existing Cooking service to read staples and review the meal-plan shopping list. Applying a review requires Cooking to advertise a verified meal-ledger capability. Set `groceries_url` to the existing Groceries service for spend, due-again and price summaries. Missing data is shown as unavailable; weeks without receipts are not treated as zero spend.

The API uses a unique `api_token` and is not published on the LAN. The ingress page is admin-only and the ingress listener accepts only the Supervisor gateway. The existing family-access Lifestyle dashboard and all legacy shopping, receipt and meal-plan paths must remain active until access, parity and cutover are independently verified.

To validate the private existing store and aisle files without changes, run `python3 tools/import_layouts.py --stores <stores.json> --aisles <aisles.json>`. After backing up the add-on and verifying the target is empty, add `--apply --url <internal-api-url>` and supply `SHOPPING_API_TOKEN` in the environment. The import is atomic and refuses a nonempty target. If interrupted, inspect the target before retrying. Do not migrate while a shop is in progress or within the household's shopping-freeze window.

Known gaps before retirement of the old dashboard include family access, live validation of the integrations, reconciliation of staple data, existing receipt intake and notification workflows, and a coordinated switch of all writers. No dry-run or local test proves a live shopping action.
