# New add-on UI template

Every IH add-on ships an ingress page (guarded by `tests/test_every_addon_has_ui.py`). A headless add-on gets this status page; that is enough.

To start a new add-on:

1. Copy `ui_server.py` to `<addon>/app/ui_server.py` and `ui.json` to `<addon>/app/ui/ui.json`. Edit title, purpose, description, `health_port`, `health_path` and the bullet list.
2. Run `ih_ui/sync_ih_ui.sh`. Because `ui.json` has `"page": "status"`, it fills `app/ui/` with the shared page, script, styles and the `ih-nav` top bar.
3. In `config.yaml` set `ingress: true`, `ingress_port: <port>`, `panel_title`, `panel_icon`, `panel_admin: true`. Start `ui_server.py` from `run.sh` in the background (it listens on `IH_UI_PORT`, default 8811).
4. Add a UI check to the add-on tests (see `ih_ui/tests/test_template.py` for the pattern: start, `/` is 200, `/healthz` is ok, parallel requests do not block).

The server is threaded, only the ingress peer may read the page, and `/healthz` stays open for the watchdog. A page that needs more than a status line replaces `index.html` and `ui.js` but keeps the `ih-nav`, the threaded server and the same `/healthz`.
