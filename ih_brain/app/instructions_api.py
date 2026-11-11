"""Instructions viewer/editor: the rules between the ingress web server and the fixed-file store.

Who may call: only requests that came through Home Assistant ingress (checked by the web server by
peer address) AND carry the signed-in user's id, which the Supervisor adds to ingress requests.
Home Assistant only gives an ingress session to administrators; the add-on cannot see the admin flag
itself, so `instructions_user_ids` can narrow access to named user ids.

Which files: a fixed table in this module. A request names a file by its short id, never by path.
"""
import difflib
import hashlib
import hmac
import json
import os

import instructions_store as store_mod
from instructions_store import Conflict, InvalidContent, InstructionStore

MODE_OFF, MODE_VIEW, MODE_EDIT = "off", "view", "edit"
MODES = (MODE_OFF, MODE_VIEW, MODE_EDIT)

# The complete list of files this feature can ever touch. `locked` files are view-only in every mode.
# The path is inside the add-on container: /share is the shared folder (mapped share:rw). No config-folder mapping is used.
FILES = {
    "house": {"label": "Agent instructions (agents.md)", "path": "/share/agents/agents.md", "locked": False},
}
DEFAULT_FILES = ("house",)

USER_HEADER = "X-Remote-User-Id"           # set by the Supervisor on ingress requests
CSRF_HEADER = "X-IH-Instructions"          # a page on another site cannot add this without a refused preflight
CSRF_VALUE = "1"
SAME_ORIGIN = "same-origin"
JSON_TYPE = "application/json"
MAX_REQUEST_BYTES = store_mod.MAX_BYTES * 6 + 4096   # JSON may escape one byte as six characters
MAX_DIFF_LINES = 4000                      # the preview is cut after this many lines
MAX_AUDIT_SHOWN = 100
MAX_USER_ID_LENGTH = 64
DIFF_CONTEXT_LINES = 3
SECRET_BYTES = 32


def clean_mode(value):
    value = str(value or "").strip().lower()
    return value if value in MODES else MODE_OFF


def clean_files(value):
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_FILES)
    return [f for f in FILES if f in value]


def clean_user_ids(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


class InstructionsEditor:
    def __init__(self, mode=MODE_OFF, files=DEFAULT_FILES, user_ids=(), work_dir="/data/instructions", table=None):
        self.mode = clean_mode(mode)
        self.user_ids = clean_user_ids(user_ids)
        self.work_dir = work_dir
        table = FILES if table is None else table
        self.table = {fid: table[fid] for fid in table if fid in files}
        self.stores = {fid: InstructionStore(fid, row["path"], work_dir) for fid, row in self.table.items()}
        self._secret = os.urandom(SECRET_BYTES)

    # ---- access --------------------------------------------------------------------------
    def _denied(self, headers):
        user = (headers.get(USER_HEADER) or "").strip()
        if not user or len(user) > MAX_USER_ID_LENGTH:
            return 401, {"error": "Home Assistant did not say who is signed in"}
        if self.user_ids and user not in self.user_ids:
            return 403, {"error": "This user may not open the instructions"}
        return None

    def _write_denied(self, headers):
        if (headers.get("Content-Type") or "").split(";")[0].strip().lower() != JSON_TYPE:
            return 415, {"error": "Send JSON"}
        if headers.get(CSRF_HEADER) != CSRF_VALUE:
            return 403, {"error": "Request did not come from the instructions page"}
        site = headers.get("Sec-Fetch-Site")
        if site is not None and site.strip().lower() != SAME_ORIGIN:
            return 403, {"error": "Request did not come from the instructions page"}
        return None

    def _store(self, file_id):
        if not isinstance(file_id, str) or file_id not in self.stores:
            raise KeyError(file_id)
        return self.stores[file_id]

    def _editable(self, file_id):
        return self.mode == MODE_EDIT and not self.table[file_id]["locked"]

    def _token(self, file_id, revision, raw):
        msg = "\0".join((file_id, revision, hashlib.sha256(raw).hexdigest())).encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    # ---- operations ----------------------------------------------------------------------
    def state(self):
        files = []
        for fid, row in self.table.items():
            files.append({"id": fid, "label": row["label"], "path": row["path"],
                          "available": self.stores[fid].available(), "editable": self._editable(fid)})
        return {"mode": self.mode, "max_bytes": store_mod.MAX_BYTES, "files": files if self.mode != MODE_OFF else []}

    def _preview(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"id", "content", "revision"}:
            return 400, {"error": "Supply id, content and revision only"}
        st = self._store(payload["id"])
        if not self._editable(payload["id"]):
            return 403, {"error": "This file is read-only"}
        raw = store_mod.validate(payload["content"])
        now = st.read()
        stale = now["revision"] != payload["revision"]
        lines = list(difflib.unified_diff(now["content"].splitlines(), payload["content"].splitlines(),
                                          "saved", "your edit", n=DIFF_CONTEXT_LINES, lineterm=""))
        added = sum(1 for l in lines[2:] if l.startswith("+"))
        removed = sum(1 for l in lines[2:] if l.startswith("-"))
        return 200, {"diff": lines[:MAX_DIFF_LINES], "truncated": len(lines) > MAX_DIFF_LINES, "added": added, "removed": removed,
                     "changed": raw != now["content"].encode("utf-8"), "stale": stale, "bytes_before": now["size"],
                     "bytes_after": len(raw),
                     "preview": None if stale else self._token(payload["id"], payload["revision"], raw)}

    def _save(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"id", "content", "revision", "preview"}:
            return 400, {"error": "Supply id, content, revision and preview only"}
        st = self._store(payload["id"])
        if not self._editable(payload["id"]):
            return 403, {"error": "This file is read-only"}
        raw = store_mod.validate(payload["content"])
        want = self._token(payload["id"], str(payload["revision"]), raw)
        if not isinstance(payload["preview"], str) or not hmac.compare_digest(want, payload["preview"]):
            return 428, {"error": "Preview the changes before saving"}
        return 200, st.save(payload["content"], payload["revision"])

    def handle(self, method, route, headers, query=None, body=None):
        """route is the part after /api/instructions/. Returns (status, JSON-able dict). Never raises."""
        query = query or {}
        denied = self._denied(headers)
        if denied:
            return denied
        try:
            if method == "GET" and route == "state":
                return 200, self.state()
            if self.mode == MODE_OFF:
                return 404, {"error": "The instructions editor is switched off"}
            if method == "GET":
                if route == "file":
                    data = self._store(query.get("id")).read()
                    return 200, dict(data, id=query.get("id"), editable=self._editable(query.get("id")))
                if route == "backups":
                    return 200, {"backups": self._store(query.get("id")).backups()}
                if route == "backup":
                    return 200, self._store(query.get("id")).read_backup(query.get("name"))
                if route == "audit":
                    return 200, {"entries": store_mod.audit_entries(self.work_dir)[:MAX_AUDIT_SHOWN]}
                return 404, {"error": "Not found"}
            if method != "POST":
                return 405, {"error": "Method not allowed"}
            if route not in ("preview", "save"):
                return 404, {"error": "Not found"}
            denied = self._write_denied(headers)
            if denied:
                return denied
            if self.mode != MODE_EDIT:
                return 403, {"error": "The instructions editor is read-only"}
            if body is None or len(body) > MAX_REQUEST_BYTES:
                return 413, {"error": "Request too large"}
            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeError):
                return 400, {"error": "Invalid JSON"}
            return self._preview(payload) if route == "preview" else self._save(payload)
        except KeyError:
            return 404, {"error": "Unknown file"}
        except Conflict as e:
            return 409, {"error": str(e)}
        except (InvalidContent, UnicodeError) as e:
            return 400, {"error": str(e) if isinstance(e, InvalidContent) else "File is not valid UTF-8 text"}
        except OSError:
            return 503, {"error": "Instructions are unavailable"}
        except Exception:
            return 500, {"error": "The instructions editor failed"}
