# Game UI components

Each game module can own its detail experience without adding game-specific markup
to the shared library page.

1. Add `UI_COMPONENT = "<slug>"` to the matching module in `app/tools/`.
2. Add `app/ui/games/<slug>.js` and register
   `window.IHGameComponents.<slug> = { render(details, helpers) { ... } }`.
3. Return details from the module's existing `details(tc, player)` function.

Current game components: `destiny` and `arc_raiders`.

The library API includes `ui_component` in the details response. The shared
shell loads that component only when the player opens the game's details. A
component receives safe DOM helpers (`el`, `section`, `row`, `dataTable`, and
`fmtDate`) plus the game ID. It should use shared IH component tags and classes
from `ui.css`, keep all game-specific presentation inside its own file, and
return a DOM node. Components run locally in the add-on; no external scripts
are loaded.
