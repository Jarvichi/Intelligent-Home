# Legacy recipe compatibility

Cooking owns the canonical recipes. Legacy stores are derived caches, never fallback writers.
The adapter accepts an injected authenticated client; credentials stay outside the browser and this package.

`GET /recipe-snapshot` is bearer-authenticated and reads the store consistently. Each recipe contains
`_revision`. Both ingress and bearer edits require that revision; a stale edit is rejected. New recipes
use the create route. Imports never replace differing existing recipes without the exact current value
in `expected_revisions`. Imports validate the whole batch before writing any recipe or side file.
Replacements keep distinct backups. Legacy identical imports are no-ops.

For an existing installation, prepare every writer before activation:

1. Inventory the existing canonical and legacy recipes. Resolve differences explicitly and keep backups.
2. Stage CLI and standalone-service routing through `RecipeOwner`. Remove local fallback writes and
   automatic full-store overwrite mirroring from the active path. Keep cooking and shopping actions intact.
3. Update every editor to retain revisions and explicit step identities. `recipe-editor.js` supplies
   reorder/add/remove controls plus bounded polling of a per-request save result for webhook clients.
   Use `recipe-editor.css` alongside the canonical `ih_ui/tokens.css` for retained pages.
4. Exercise the real HTTP contract, stale/concurrent writes, outages and cache-refresh failures with
   temporary data. Check retained pages at phone/desktop widths in light/dark without real saves.
5. Install Cooking 0.6.8 or newer, refresh only derived caches from one authenticated snapshot, and
   activate the prepared legacy routes together. Do not activate import guards as an isolated migration.
6. Verify recipe parity, loaded revisions, read-only pages and service health. An outage must fail closed.

Cache refreshes are serialized across processes and keep snapshots before replacement. Canonical save
success followed by cache failure must be shown as uncertain refresh, never retried automatically.
A client without a revision must reload; never synthesize the current revision when saving an old draft.
Keep meal-plan ownership and household shopping actions separate until their migration is verified.
