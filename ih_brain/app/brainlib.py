"""Brain notes engine for ih_brain: a stdlib-only port of the House Brain tool code (same tool names, arguments and result text).
Differences from the Home Assistant component: no Home Assistant imports; the search index lives in the data folder (not in the notes
folder); read_only mode refuses writes; search falls back to a plain scan if SQLite has no FTS5. Notes are plain markdown files."""
import json
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
MAX_NOTE = 60000
READ_ONLY_MSG = "refused: this brain server is read-only (option read_only is true). Ask the owner to set read_only to false in the ih_brain add-on to allow writes."
PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /_\-.()&',]*\.md$")   # Obsidian-style names (capitals, spaces) are allowed since 5 Oct 2026
SECRET_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9_\-+/=]{10,}"
    r"|eyJ[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY")
WIKI_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def _in_area(rel, area):
    """True if the note is under `area`: a path prefix (technology/network) or any folder of that name (garden)."""
    a = area.strip("/")
    return bool(a) and (rel.startswith(a + "/") or rel == a or ("/" + a + "/") in ("/" + rel))


def _tool(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("brain_help", "READ THIS FIRST. How the House Brain works, the note conventions and the rules for writing notes."),
    _tool("brain_index", "The top-level index (INDEX.md): every area and the key notes. Start here to find things."),
    _tool("brain_search", "Full-text search over all notes. Returns ranked paths with snippets.",
          {"query": {"type": "string"}, "area": {"type": "string", "description": "optional folder, e.g. garden or technology/network (matches a path prefix or any folder with that name)"},
           "limit": {"type": "integer", "description": "default 8"}}, ["query"]),
    _tool("brain_read", "Read one note by path (e.g. garden/overview.md).", {"path": {"type": "string"}}, ["path"]),
    _tool("brain_list", "List notes (path, title, type, status, updated), optionally under a folder.",
          {"area": {"type": "string"}}),
    _tool("brain_recent", "The most recently changed notes.", {"limit": {"type": "integer"}}),
    _tool("brain_image", "Return one image stored in the brain (png, jpg, gif, webp, svg; max 6 MB) as a data URL, for showing "
          "![alt](path) in the browser page. path = path under the brain folder (e.g. attachments/20261006/photo.jpg) or just the file name.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("brain_text", "Return one code or text attachment stored in the brain (jsx, tsx, js, json, csv, txt; max 1 MB) as plain text, "
          "so the browser page can render JSX/TSX safely in a sandbox. path = path under the brain folder (e.g. attachments/20261007/planner.tsx) or just the file name.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("brain_file", "Return one document stored in the brain (pdf, zip, xlsx, docx, pptx, png, jpg, gif, webp, svg, tsx, jsx; max 20 MB) "
          "as a data URL so the browser page can open it. path = path under the brain folder or just the file name.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("brain_files", "List the jsx/tsx files stored in the brain (one path per line) so the browser page can show them in its tree as apps."),
    _tool("brain_links", "Outgoing [[links]] and backlinks (notes that link to this one).", {"path": {"type": "string"}}, ["path"]),
    _tool("brain_write", "Create or replace a note. Small notes, one idea each, with the WHY. Use front matter (title, type, status, related). "
          "Never include passwords/tokens/keys. The old version is kept in .history.",
          {"path": {"type": "string", "description": "lowercase, folders ok, ends .md, e.g. garden/watering-schedule.md"},
           "content": {"type": "string"}, "author": {"type": "string", "description": "who you are, e.g. pi-claude, pc-claude"},
           "summary": {"type": "string", "description": "one line: what changed and why"}}, ["path", "content", "author"]),
    _tool("brain_graph", "All notes and the [[links]] between them as JSON {nodes:[{id,t,g,n}], links:[{s,t}]} (for the browser page's graph view)."),
    _tool("brain_append", "Append text to an existing note (e.g. a dated line in a log note).",
          {"path": {"type": "string"}, "text": {"type": "string"}, "author": {"type": "string"}}, ["path", "text", "author"]),
]


class Brain:
    def __init__(self, base, data_dir="/data", read_only=True):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.read_only = read_only
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.db = Path(data_dir) / "index.sqlite"
        self.last_index = None

    # ---------------------------------------------------------------- files
    def _safe(self, path):
        p = (path or "").strip().lstrip("/")
        if not PATH_RE.match(p) or ".." in p or p.startswith(".") or "/." in p:
            raise ValueError("bad path: use letters, digits, spaces, - _ . ( ) & and /, ending in .md (e.g. technology/network/overview.md)")
        full = (self.base / p).resolve()
        if self.base.resolve() not in full.parents:
            raise ValueError("path escapes the brain folder")
        return full

    def _notes(self, area=""):
        out = []
        for f in sorted(self.base.rglob("*.md")):
            rel = f.relative_to(self.base).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            if area and not _in_area(rel, area):
                continue
            out.append((rel, f))
        return out

    @staticmethod
    def _front(text):
        m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
        meta = {}
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        return meta, (text[m.end():] if m else text)

    def _title(self, rel, text):
        meta, body = self._front(text)
        if meta.get("title"):
            return meta["title"]
        h = re.search(r"^#\s+(.+)$", body, re.M)
        return h.group(1).strip() if h else rel

    # ---------------------------------------------------------------- search index
    def _signature(self):
        files = self._notes()
        return f"{len(files)}:{max((f.stat().st_mtime_ns for _, f in files), default=0)}"

    def _reindex(self):
        sig = self._signature()
        con = sqlite3.connect(self.db)
        try:
            con.execute("create table if not exists meta(k text primary key, v text)")
            row = con.execute("select v from meta where k='sig'").fetchone()
            if row and row[0] == sig:
                return con
            con.execute("drop table if exists notes")
            con.execute("create virtual table notes using fts5(path, title, body, tokenize='porter unicode61')")
            for rel, f in self._notes():
                t = f.read_text(errors="replace")
                con.execute("insert into notes values (?,?,?)", (rel, self._title(rel, t), t))
            con.execute("insert or replace into meta values('sig', ?)", (sig,))
            con.commit()
            self.last_index = datetime.now().isoformat(timespec="seconds")
            return con
        except Exception:
            con.close()
            raise

    # ---------------------------------------------------------------- tools
    def t_brain_help(self, a):
        readme = self.base / "README.md"
        return readme.read_text() if readme.exists() else "No README.md yet in the brain folder."

    def t_brain_index(self, a):
        idx = self.base / "INDEX.md"
        return idx.read_text() if idx.exists() else "No INDEX.md yet."

    def t_brain_search(self, a):
        q = (a.get("query") or "").strip()
        limit = max(1, min(int(a.get("limit") or 8), 30))
        area = (a.get("area") or "").strip("/")
        stop = {"the", "and", "for", "why", "how", "what", "does", "did", "are", "was", "with", "this", "that", "from", "have", "has", "can", "you", "our", "its", "who", "when", "where"}
        terms = [t for t in re.findall(r"[A-Za-z0-9_]+", q) if len(t) > 2 and t.lower() not in stop] or re.findall(r"[A-Za-z0-9_]+", q)
        if not terms:
            return "Give a query with at least one word."
        match = " OR ".join(f'"{t}"*' for t in terms)   # any word matches; best (bm25) matches rank first
        # Weighted ranking (17 Oct 2026): a match in the title counts x10, in the path x5, in the body x1.
        # Measured on the real index: about +0.3 ms per search. Columns are (path, title, body).
        try:
            con = self._reindex()
            try:
                rows = con.execute("select path, title, snippet(notes, 2, '[', ']', '…', 14) from notes where notes match ? order by bm25(notes, 5.0, 10.0, 1.0) limit 60", (match,)).fetchall()
            finally:
                con.close()
        except sqlite3.OperationalError:   # no FTS5 in this SQLite: plain scan, title hits first
            rows = self._scan(terms)
        rows = [r for r in rows if not area or _in_area(r[0], area)][:limit]
        if not rows:
            return f"No notes match '{q}'."
        return "\n".join(f"- {p} — {t}\n    {s.replace(chr(10), ' ')}" for p, t, s in rows)

    def _scan(self, terms):
        scored = []
        for rel, f in self._notes():
            t = f.read_text(errors="replace")
            low, title = t.lower(), self._title(rel, t).lower()
            score = sum(10 * (w.lower() in title) + 5 * (w.lower() in rel.lower()) + min(low.count(w.lower()), 5) for w in terms)
            if score:
                i = min((low.find(w.lower()) for w in terms if w.lower() in low), default=0)
                scored.append((-score, rel, self._title(rel, t), t[max(0, i - 40):i + 80]))
        return [(r, ti, sn) for _, r, ti, sn in sorted(scored)[:60]]

    def t_brain_read(self, a):
        f = self._safe(a.get("path"))
        if not f.exists():
            return "No such note. Use brain_search or brain_list."
        return f.read_text()

    IMG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                 ".svg": "image/svg+xml"}

    def t_brain_image(self, a):
        """Read-only: an image file inside the brain folder as a data URL. Only image types, hidden folders skipped, size capped."""
        import base64
        raw = (a.get("path") or "").strip().lstrip("/")
        name = Path(raw).name
        ext = Path(name).suffix.lower()
        if not raw or ext not in self.IMG_TYPES or ".." in raw or raw.startswith(".") or "/." in raw:
            raise ValueError("not an image path (png, jpg, jpeg, gif, webp, svg under the brain folder)")
        base = self.base.resolve()
        f = (base / raw).resolve()
        if not (f.is_file() and base in f.parents):
            f = None
            for c in sorted(base.rglob(name)):
                rel = c.relative_to(base).as_posix()
                if c.is_file() and not rel.startswith(".") and "/." not in rel and base in c.resolve().parents:
                    f = c.resolve()
                    break
        if not f:
            raise ValueError("no such image in the brain")
        if f.stat().st_size > 6 * 1024 * 1024:
            raise ValueError("image is bigger than 6 MB")
        return "data:%s;base64,%s" % (self.IMG_TYPES[ext], base64.b64encode(f.read_bytes()).decode())

    TEXT_TYPES = {".jsx", ".tsx", ".js", ".json", ".csv", ".txt"}

    def t_brain_text(self, a):
        """Read-only: a code/text attachment inside the brain folder as text. Hidden folders skipped, size capped, no notes (.md) so
        privacy rules for notes are not bypassed."""
        raw = (a.get("path") or "").strip().lstrip("/")
        name = Path(raw).name
        ext = Path(name).suffix.lower()
        if not raw or ext not in self.TEXT_TYPES or ".." in raw or raw.startswith(".") or "/." in raw:
            raise ValueError("not a text attachment path (jsx, tsx, js, json, csv, txt under the brain folder)")
        base = self.base.resolve()
        f = (base / raw).resolve()
        if not (f.is_file() and base in f.parents):
            f = None
            for c in sorted(base.rglob(name)):
                rel = c.relative_to(base).as_posix()
                if c.is_file() and not rel.startswith(".") and "/." not in rel and base in c.resolve().parents:
                    f = c.resolve()
                    break
        if not f:
            raise ValueError("no such file in the brain")
        if f.stat().st_size > 1024 * 1024:
            raise ValueError("file is bigger than 1 MB")
        return f.read_text(errors="replace")

    FILE_TYPES = {".pdf": "application/pdf", ".zip": "application/zip", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  ".tsx": "text/plain", ".jsx": "text/plain", ".txt": "text/plain", ".csv": "text/csv", **IMG_TYPES}

    def t_brain_file(self, a):
        """Read-only: a document (not a note) inside the brain folder as a data URL, for opening from the browser page (12 Oct 2026).
        Same rules as brain_image: listed types only, hidden folders skipped, size capped."""
        import base64
        raw = (a.get("path") or "").strip().lstrip("/")
        name = Path(raw).name
        ext = Path(name).suffix.lower()
        if not raw or ext not in self.FILE_TYPES or ".." in raw or raw.startswith(".") or "/." in raw:
            raise ValueError("not a document path (pdf, zip, office files, images under the brain folder)")
        base = self.base.resolve()
        f = (base / raw).resolve()
        if not (f.is_file() and base in f.parents):
            f = None
            for c in sorted(base.rglob(name)):
                rel = c.relative_to(base).as_posix()
                if c.is_file() and not rel.startswith(".") and "/." not in rel and base in c.resolve().parents:
                    f = c.resolve()
                    break
        if not f:
            raise ValueError("no such file in the brain")
        if f.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("file is bigger than 20 MB")
        return "data:%s;base64,%s" % (self.FILE_TYPES[ext], base64.b64encode(f.read_bytes()).decode())

    def t_brain_files(self, a):
        """Read-only: paths of jsx/tsx attachments (hidden folders skipped), newest first."""
        base = self.base.resolve()
        out = []
        for f in base.rglob("*"):
            rel = f.relative_to(base).as_posix()
            if f.suffix.lower() in (".jsx", ".tsx") and f.is_file() and not rel.startswith(".") and "/." not in rel:
                out.append((f.stat().st_mtime, rel))
        return "\n".join("- " + r for _, r in sorted(out, reverse=True)) or "No files."

    def t_brain_list(self, a):
        lines = []
        for rel, f in self._notes(a.get("area") or ""):
            t = f.read_text(errors="replace")
            meta, _ = self._front(t)
            lines.append(f"- {rel} — {self._title(rel, t)} [{meta.get('type', '?')}/{meta.get('status', '?')}] updated {meta.get('updated', '?')}")
        return "\n".join(lines) or "No notes."

    def t_brain_recent(self, a):
        limit = max(1, min(int(a.get("limit") or 12), 50))
        files = sorted(self._notes(), key=lambda x: x[1].stat().st_mtime, reverse=True)[:limit]
        return "\n".join(f"- {rel} — {self._title(rel, f.read_text(errors='replace'))} ({datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M})" for rel, f in files)

    def t_brain_links(self, a):
        f = self._safe(a.get("path"))
        if not f.exists():
            return "No such note."
        rel = f.relative_to(self.base.resolve()).as_posix()
        stem = Path(rel).stem
        title = self._title(rel, f.read_text()).lower()
        out = sorted({w.strip() for w in WIKI_RE.findall(f.read_text())})
        back = []
        for r, ff in self._notes():
            if r == rel:
                continue
            if any(w.strip().lower() in (stem.lower(), title, rel.lower().removesuffix(".md")) for w in WIKI_RE.findall(ff.read_text(errors="replace"))):
                back.append(r)
        return "Outgoing: " + (", ".join(out) or "none") + "\nBacklinks: " + (", ".join(back) or "none")

    def t_brain_graph(self, a):
        notes = [(rel, f.read_text(errors="replace")) for rel, f in self._notes()]
        key = {}
        for rel, text in notes:
            for k in (rel.lower().removesuffix(".md"), Path(rel).stem.lower(), self._title(rel, text).lower()):
                key.setdefault(k, rel)
        nodes, links, seen = [], [], set()
        for rel, text in notes:
            nodes.append({"id": rel, "t": self._title(rel, text), "g": rel.split("/")[0] if "/" in rel else "", "n": len(text)})
            for w in WIKI_RE.findall(text):
                tgt = key.get(w.strip().lower().removesuffix(".md"))
                if tgt and tgt != rel and (rel, tgt) not in seen:
                    seen.add((rel, tgt)); links.append({"s": rel, "t": tgt})
        return json.dumps({"nodes": nodes, "links": links}, separators=(",", ":"))

    def _write(self, path, content, author, summary):
        if self.read_only:
            raise ValueError(READ_ONLY_MSG)
        f = self._safe(path)
        if len(content) > MAX_NOTE:
            raise ValueError("note too long (max 60 KB): split it into smaller linked notes")
        if SECRET_RE.search(content):
            raise ValueError("refused: this looks like it contains a password, token or key. Never store secrets in the brain; say where the secret lives instead.")
        meta, body = self._front(content)
        today = datetime.now().strftime("%Y-%m-%d")
        if not meta:
            t = self._title(f.name, content)
            content = f"---\ntitle: {t}\ntype: note\nstatus: current\nupdated: {today}\n---\n" + content
        else:
            if re.search(r"^updated:.*$", content.split("\n---", 1)[0], re.M):
                content = re.sub(r"^updated:.*$", f"updated: {today}", content, count=1, flags=re.M)
            else:
                content = content.replace("\n---", f"\nupdated: {today}\n---", 1)
        with self.lock:
            f.parent.mkdir(parents=True, exist_ok=True)
            if f.exists():
                hist = self.base / ".history" / Path(path).with_suffix("")
                hist.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, hist / f"{datetime.now():%Y%m%d-%H%M%S}.md")
                old = sorted(hist.glob("*.md"))
                for o in old[:-20]:
                    o.unlink()
            tmp = f.with_suffix(".tmp")
            tmp.write_text(content)
            os.replace(tmp, f)
            with open(self.base / "_log.md", "a") as lg:
                lg.write(f"- {datetime.now():%Y-%m-%d %H:%M} | {author or '?'} | {path} | {(summary or '').strip()[:160]}\n")
        return f"saved {path} ({len(content)} bytes)"

    def t_brain_write(self, a):
        return self._write(a.get("path"), a.get("content") or "", a.get("author"), a.get("summary"))

    def t_brain_append(self, a):
        if self.read_only:
            raise ValueError(READ_ONLY_MSG)
        f = self._safe(a.get("path"))
        if not f.exists():
            return "No such note: create it with brain_write first."
        text = a.get("text") or a.get("content") or ""   # 12 Oct 2026: clients send "content" (as for brain_write); only "text" was read, so appends were silently empty
        if not text.strip():
            return "Nothing to append (send the text as 'content')."
        return self._write(a.get("path"), f.read_text().rstrip("\n") + "\n" + text.strip("\n") + "\n", a.get("author"), "append")
