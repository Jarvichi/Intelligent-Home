# IH UI package

The canonical, dependency-free UI tokens and native Web Component primitives for Intelligent Home add-ons. Pages load these local files from their own ingress origin; no CDN or third-party runtime is used.

Run `ih_ui/sync_ih_ui.sh` after changing canonical assets. Run it with `--check` to detect missing or stale copies in add-ons that contain `app/ui/`. Optional `patterns.css` is copied only to pages that have explicitly adopted `ih-patterns.css`; those pages must also serve and load it. Every IH add-on has an ingress page. Most use the standard status-console sidecar (`app/ui_server.py`); Cooking, Shopping, Orchestrator and Brain serve their richer page from the existing app server and expose the same assets through their Supervisor-only ingress handler.

New pages use `tokens.css` and `components.js`, relative asset/API URLs, `ih-` elements, and the requirements in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`.

The local [UI consistency plan](consistency.md) describes reusable Games patterns and their rollout across add-ons. The normative guide and durable design decisions live in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`.

## Top bar (`ih-nav`)

Every page puts `<ih-nav data-home-href="/app/32c8114c_ih_orchestrator" data-title="Name"></ih-nav>` as the first child of `<body>` (before `<ih-app>`) and loads `ih-ui.js`. It is sticky, with 44px targets. The hub omits `data-home-href`. Sub-screens call `IHNav.setTrail([{label: 'Parent', onClick: fn}], 'Title')` (empty list returns to the top-level look); `IHNav.goTop(href)` navigates the whole Home Assistant window (tested live: a plain link inside the ingress frame opens Home Assistant nested inside the frame, so links to other `/app/...` pages need `target="_top"`). Remaining adopters: Cooking, Brain, Shopping. Tests: `python3 -m unittest discover -s ih_ui/tests`.
