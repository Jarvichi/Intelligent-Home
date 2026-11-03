# IH UI package

The canonical, dependency-free UI tokens and native Web Component primitives for Intelligent Home add-ons. Pages load these local files from their own ingress origin; no CDN or third-party runtime is used.

Run `ih_ui/sync_ih_ui.sh` after changing either canonical asset. Run it with `--check` to detect missing or stale copies in add-ons that already contain `app/ui/`.

New pages use `tokens.css` and `components.js`, relative asset/API URLs, `ih-` elements, and the requirements in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`.
