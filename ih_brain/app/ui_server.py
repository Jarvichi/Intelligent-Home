"""Supervisor-ingress-only web UI for the House Brain: read-only note reader, plus the optional instructions editor."""
import json
import mimetypes
import os
import re
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import instructions_api

GATEWAY_PEER = "172.30.32.2"
PORT = 8821
UI_DIR = Path(__file__).resolve().parent / "ui"
MAX_QUERY = 300
INSTRUCTIONS_API = "/api/instructions/"
INSTRUCTIONS_ASSETS = ("/instructions.css", "/instructions.js")


ORPHAN_RULE = ("A note is an orphan when no other note links to it. Links are matched exactly as the map matches them: "
               "[[...]] text equal to the note's path, file name or title.")
SNIPPET = 170


def _is_private(meta):
    # Same reading as the sharing rule in brainlib: only share: true is shareable; share: false is marked private.
    return str(meta.get("share", "")).strip().strip("\"'").lower() == "false"


def _plain_excerpt(body, limit=SNIPPET):
    text = re.sub(r"```.*?```", " ", body, flags=re.S)
    text = re.sub(r"^\s{0,3}#{1,6}\s+.*$", " ", text, flags=re.M)
    text = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"[*_`>|#\[\]]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def _date_of(meta, mtime):
    value = str(meta.get("updated", "")).strip().strip("\"'")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return time.strftime("%Y-%m-%d", time.gmtime(mtime))


def _note_rows(brain, area="", excerpt=False):
    rows = []
    for rel, f in brain._notes(area):
        try:
            raw = f.read_text(errors="replace")
            mtime = f.stat().st_mtime
            meta, body = brain._front(raw)
            row = {"path": rel, "title": brain._title(rel, raw), "type": meta.get("type", "note"),
                   "status": meta.get("status", ""), "updated": meta.get("updated", ""),
                   "date": _date_of(meta, mtime), "mtime": int(mtime), "private": _is_private(meta)}
            if excerpt:
                row["excerpt"] = _plain_excerpt(body)
            rows.append(row)
        except OSError:
            continue
    return rows


def _summary(brain, now=None):
    """Counts for the page's 'What's in the brain' card. Orphans use the map's link rule (see ORPHAN_RULE)."""
    now = time.time() if now is None else now
    graph = json.loads(brain.t_brain_graph({}))
    linked = {link["t"] for link in graph["links"]}
    total = changed = drafts = private = shareable = 0
    areas = set()
    for rel, f in brain._notes():
        try:
            raw = f.read_text(errors="replace")
            mtime = f.stat().st_mtime
        except OSError:
            continue
        meta, _ = brain._front(raw)
        total += 1
        areas.add(rel.split("/", 1)[0] if "/" in rel else "")
        if now - mtime <= 7 * 86400:
            changed += 1
        if str(meta.get("status", "")).strip().lower() == "draft":
            drafts += 1
        if _is_private(meta):
            private += 1
        elif str(meta.get("share", "")).strip().lower() == "true":
            shareable += 1
    orphans = sum(1 for node in graph["nodes"] if node["id"] not in linked)
    return {"notes": total, "links": len(graph["links"]), "areas": len(areas), "changed_7d": changed,
            "drafts": drafts, "orphans": orphans, "orphan_rule": ORPHAN_RULE, "private": private,
            "shareable": shareable, "unmarked": total - private - shareable}


def _links_of(brain, path):
    """Outgoing and incoming links of one note, from the same graph data the map uses."""
    graph = json.loads(brain.t_brain_graph({}))
    titles = {n["id"]: n["t"] for n in graph["nodes"]}
    out = [{"path": l["t"], "title": titles.get(l["t"], l["t"])} for l in graph["links"] if l["s"] == path]
    back = [{"path": l["s"], "title": titles.get(l["s"], l["s"])} for l in graph["links"] if l["t"] == path]
    key = lambda r: (r["title"].casefold(), r["path"])
    return {"path": path, "out": sorted(out, key=key), "in": sorted(back, key=key)}


def _graph_summary(brain):
    # Keep every note and edge: the browser's map is zoomable and searchable.
    return json.loads(brain.t_brain_graph({}))


def make_handler(brain, editor=None):
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
            if path in ("/ih-ui.css", "/ih-ui.js", "/ih-patterns.css", "/ui.css", "/ui.js", "/markdown.js", "/logic.js", "/ui.json", "/force-graph.min.js"):
                return self._file("vendor/force-graph-1.43.5.min.js" if path == "/force-graph.min.js" else path[1:])
            if path == "/instructions":
                return self._file("instructions.html")
            if path in INSTRUCTIONS_ASSETS:
                return self._file(path[1:])
            if path.startswith(INSTRUCTIONS_API):
                return self._instructions("GET", path, {k: v[0] for k, v in query.items()})
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
                    rows = _note_rows(brain, excerpt=True)
                    rows.sort(key=lambda r: (r["mtime"], r["path"]), reverse=True)
                    return self._send(200, {"notes": rows})
                if path == "/api/summary":
                    return self._send(200, _summary(brain))
                if path == "/api/links":
                    note_path = (query.get("path") or [""])[0]
                    note = brain._safe(note_path)
                    if not note.is_file():
                        return self._send(404, {"error": "Note not found"})
                    return self._send(200, _links_of(brain, note.relative_to(brain.base.resolve()).as_posix()))
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
                                excerpt = body[:1800].strip()  # line breaks kept: the page strips headings and code itself
                                rows.append((matched, {"path": rel, "title": title, "type": meta.get("type", "note"),
                                                      "status": meta.get("status", ""), "updated": meta.get("updated", ""),
                                                      "date": _date_of(meta, f.stat().st_mtime), "private": _is_private(meta),
                                                      "excerpt": excerpt}))
                    rows.sort(key=lambda x: (-x[0], x[1]["title"].casefold(), x[1]["path"]))
                    return self._send(200, {"query": q, "notes": [r for _, r in rows]})
                if path == "/api/read":
                    note_path = (query.get("path") or [""])[0]
                    note = brain._safe(note_path)
                    if not note.is_file():
                        return self._send(404, {"error": "Note not found"})
                    raw = note.read_text(errors="replace")
                    meta, _ = brain._front(raw)
                    return self._send(200, {"path": note.relative_to(brain.base.resolve()).as_posix(),
                                            "title": brain._title(note_path, raw), "content": raw,
                                            "private": _is_private(meta), "status": meta.get("status", ""),
                                            "date": _date_of(meta, note.stat().st_mtime)})
                if path == "/api/graph":
                    return self._send(200, _graph_summary(brain))
                return self._send(404, {"error": "Not found"})
            except (ValueError, OSError):
                return self._send(400, {"error": "Invalid or unavailable Brain path"})
            except Exception:
                return self._send(503, {"error": "Brain data is temporarily unavailable"})

        def do_POST(self):
            if self._blocked():
                return
            path = urlsplit(self.path).path.rstrip("/")
            if not path.startswith(INSTRUCTIONS_API):
                return self._send(404, {"error": "Not found"})
            if editor is None:
                return self._send(404, {"error": "The instructions editor is switched off"})
            try:
                length = int(self.headers.get("Content-Length") or "")
            except ValueError:
                return self._send(411, {"error": "Content-Length required"})
            if length < 0 or length > instructions_api.MAX_REQUEST_BYTES:
                return self._send(413, {"error": "Request too large"})
            return self._instructions("POST", path, {}, self.rfile.read(length))

        def _instructions(self, method, path, query, body=None):
            if editor is None:
                return self._send(404, {"error": "The instructions editor is switched off"})
            code, data = editor.handle(method, path[len(INSTRUCTIONS_API):], self.headers, query, body)
            return self._send(code, data)

        def _file(self, name):
            file = (UI_DIR / name).resolve()
            if UI_DIR not in file.parents or not file.is_file():
                return self._send(404, {"error": "Not found"})
            return self._send(200, file.read_text(encoding="utf-8"), mimetypes.guess_type(name)[0] or "text/plain")

        def log_message(self, *_):
            pass
    return Handler


def serve(brain, editor=None):
    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(brain, editor))
    threading.Thread(target=server.serve_forever, name="ih-brain-ui", daemon=True).start()
    return server
