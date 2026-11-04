"""Read-only, Supervisor-ingress-only web UI for the House Brain."""
import json
import mimetypes
import os
import re
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

GATEWAY_PEER = "172.30.32.2"
PORT = 8821
UI_DIR = Path(__file__).resolve().parent / "ui"
MAX_QUERY = 300


def _note_rows(brain, area=""):
    rows = []
    for rel, f in brain._notes(area):
        try:
            raw = f.read_text(errors="replace")
            meta, _ = brain._front(raw)
            rows.append({"path": rel, "title": brain._title(rel, raw), "type": meta.get("type", "note"),
                         "status": meta.get("status", ""), "updated": meta.get("updated", "")})
        except OSError:
            continue
    return rows


def _graph_summary(brain):
    # Keep every note and edge: the browser's map is zoomable and searchable.
    return json.loads(brain.t_brain_graph({}))


def make_handler(brain):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, content_type="application/json; charset=utf-8"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(data)

        def _blocked(self):
            if self.client_address[0] != GATEWAY_PEER:
                self._send(403, {"error": "Home Assistant ingress required"})
                return True
            return False

        def do_GET(self):
            if self._blocked():
                return
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            query = parse_qs(parts.query, keep_blank_values=True)
            if path == "/" or path == "/index.html":
                return self._file("index.html")
            if path in ("/ih-ui.css", "/ih-ui.js", "/ih-patterns.css", "/ui.css", "/ui.js", "/ui.json", "/force-graph.min.js"):
                return self._file("vendor/force-graph-1.43.5.min.js" if path == "/force-graph.min.js" else path[1:])
            if not path.startswith("/api/"):
                return self._send(404, {"error": "Not found"})
            try:
                if path == "/api/index":
                    rows = _note_rows(brain)
                    counts = Counter((r["path"].split("/", 1)[0] if "/" in r["path"] else "") for r in rows)
                    titles = {}
                    for row in rows:
                        area = row["path"].split("/", 1)[0] if "/" in row["path"] else ""
                        titles.setdefault(area, area or "General")
                    areas = [{"path": p, "title": titles[p], "count": counts[p]} for p in sorted(counts)]
                    return self._send(200, {"count": len(rows), "areas": areas, "notes": rows})
                if path == "/api/notes":
                    area = (query.get("area") or [""])[0]
                    if len(area) > 120 or ".." in area or area.startswith("."):
                        return self._send(400, {"error": "Invalid area"})
                    return self._send(200, {"notes": _note_rows(brain, area)})
                if path == "/api/recent":
                    rows = _note_rows(brain)
                    rows.sort(key=lambda r: ((brain.base / r["path"]).stat().st_mtime_ns if (brain.base / r["path"]).exists() else 0), reverse=True)
                    return self._send(200, {"notes": rows})
                if path == "/api/search":
                    q = (query.get("q") or [""])[0].strip()
                    if len(q) > MAX_QUERY:
                        return self._send(400, {"error": "Search text is too long"})
                    terms = [t.casefold() for t in re.findall(r"[\w-]+", q) if len(t) > 1]
                    rows = []
                    if terms:
                        for rel, f in brain._notes():
                            raw = f.read_text(errors="replace")
                            meta, body = brain._front(raw)
                            title = brain._title(rel, raw)
                            haystack = (rel + " " + title + " " + body).casefold()
                            matched = sum(haystack.count(term) for term in terms)
                            if matched:
                                excerpt = re.sub(r"\s+", " ", body[:1800]).strip()
                                rows.append((matched, {"path": rel, "title": title, "type": meta.get("type", "note"),
                                                      "status": meta.get("status", ""), "updated": meta.get("updated", ""),
                                                      "excerpt": excerpt}))
                    rows.sort(key=lambda x: (-x[0], x[1]["title"].casefold(), x[1]["path"]))
                    return self._send(200, {"query": q, "notes": [r for _, r in rows]})
                if path == "/api/read":
                    note_path = (query.get("path") or [""])[0]
                    note = brain._safe(note_path)
                    if not note.is_file():
                        return self._send(404, {"error": "Note not found"})
                    raw = note.read_text(errors="replace")
                    return self._send(200, {"path": note.relative_to(brain.base.resolve()).as_posix(),
                                            "title": brain._title(note_path, raw), "content": raw})
                if path == "/api/graph":
                    return self._send(200, _graph_summary(brain))
                return self._send(404, {"error": "Not found"})
            except (ValueError, OSError):
                return self._send(400, {"error": "Invalid or unavailable Brain path"})
            except Exception:
                return self._send(503, {"error": "Brain data is temporarily unavailable"})

        def _file(self, name):
            file = (UI_DIR / name).resolve()
            if UI_DIR not in file.parents or not file.is_file():
                return self._send(404, {"error": "Not found"})
            return self._send(200, file.read_text(encoding="utf-8"), mimetypes.guess_type(name)[0] or "text/plain")

        def log_message(self, *_):
            pass
    return Handler


def serve(brain):
    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(brain))
    threading.Thread(target=server.serve_forever, name="ih-brain-ui", daemon=True).start()
    return server
