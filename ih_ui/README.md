# IH UI package

The canonical, dependency-free UI tokens and native Web Component primitives for Intelligent Home add-ons. Pages load these local files from their own ingress origin; no CDN or third-party runtime is used.

Run `ih_ui/sync_ih_ui.sh` after changing canonical assets. Run it with `--check` to detect missing or stale copies in add-ons that contain `app/ui/`. Optional `patterns.css` is copied only to pages that have explicitly adopted `ih-patterns.css`; those pages must also serve and load it. Every IH add-on has an ingress page. Most use the standard status-console sidecar (`app/ui_server.py`); Cooking, Shopping, Orchestrator and Brain serve their richer page from the existing app server and expose the same assets through their Supervisor-only ingress handler.

New pages use `tokens.css` and `components.js`, relative asset/API URLs, `ih-` elements, and the requirements in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`.

The local [UI consistency plan](consistency.md) describes reusable Games patterns and the proposed rollout across add-ons. It is source documentation; updating the Brain guide requires separate Brain write access.
