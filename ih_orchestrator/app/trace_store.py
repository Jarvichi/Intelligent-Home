"""Bounded, content-free request traces for the IH operations console."""
import json
import math
import os
import re
import threading
import time

TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
SPAN_ID = re.compile(r"^[a-f0-9]{16}$")
TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
STATUSES = {"ok", "error", "skipped", "unknown"}
MAX_BODY_SPANS = 40
MAX_SPANS = 5000
RETENTION_S = 7 * 86400


class TraceStore:
    def __init__(self, path="/data/traces.json", clock=time.time):
        self.path, self.clock = path, clock
        self.lock = threading.RLock()
        self.spans = self._load()
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    def _cleanup_loop(self):
        while True:
            time.sleep(3600)
            with self.lock:
                if self._prune(self.clock()):
                    try:
                        self._save()
                    except OSError:
                        pass

    def _load(self):
        try:
            data = json.load(open(self.path))
            if isinstance(data, list):
                return [x for x in data if self._valid_saved(x)][-MAX_SPANS:]
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            pass
        return []

    @staticmethod
    def _valid_saved(x):
        if not isinstance(x, dict) or set(x) != {"trace_id", "span_id", "parent_span_id", "component", "operation", "status", "reason", "started_at", "ended_at", "duration_ms"}:
            return False
        if not TRACE_ID.fullmatch(str(x.get("trace_id") or "")) or not SPAN_ID.fullmatch(str(x.get("span_id") or "")):
            return False
        parent = x.get("parent_span_id")
        if parent is not None and not SPAN_ID.fullmatch(str(parent)):
            return False
        if not TOKEN.fullmatch(str(x.get("component") or "")) or not TOKEN.fullmatch(str(x.get("operation") or "")) or x.get("status") not in STATUSES:
            return False
        reason = str(x.get("reason") or "")
        if reason and not TOKEN.fullmatch(reason):
            return False
        try:
            started, ended, duration = float(x.get("started_at")), float(x.get("ended_at")), int(x.get("duration_ms"))
            return math.isfinite(started) and math.isfinite(ended) and 0 <= duration <= 3600000
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _clean_span(trace_id, raw, now):
        if not isinstance(raw, dict):
            return None
        if set(raw) != {"span_id", "parent_span_id", "component", "operation", "status", "reason", "started_at", "duration_ms"}:
            return None
        sid, parent = str(raw.get("span_id") or ""), raw.get("parent_span_id")
        component, operation, status = str(raw.get("component") or ""), str(raw.get("operation") or ""), str(raw.get("status") or "")
        if not SPAN_ID.fullmatch(sid) or (parent is not None and not SPAN_ID.fullmatch(str(parent))):
            return None
        if not TOKEN.fullmatch(component) or not TOKEN.fullmatch(operation) or status not in STATUSES:
            return None
        try:
            duration = int(raw.get("duration_ms"))
            started = float(raw.get("started_at"))
        except (TypeError, ValueError, OverflowError):
            return None
        if duration < 0 or duration > 3600000 or not math.isfinite(started) or started < now - 3600 or started > now + 10:
            return None
        reason = str(raw.get("reason") or "")
        if reason and not TOKEN.fullmatch(reason):
            return None
        return {"trace_id": trace_id, "span_id": sid, "parent_span_id": parent,
                "component": component, "operation": operation, "status": status,
                "reason": reason[:48], "started_at": started,
                "ended_at": started + duration / 1000.0, "duration_ms": duration}

    def _prune(self, now):
        cutoff = now - RETENTION_S
        original = len(self.spans)
        self.spans = [s for s in self.spans if s.get("ended_at", 0) >= cutoff]
        if len(self.spans) > MAX_SPANS:
            traces = {}
            for s in self.spans:
                traces.setdefault(s["trace_id"], []).append(s)
            for tid in sorted(traces, key=lambda key: max(s["ended_at"] for s in traces[key])):
                if len(self.spans) <= MAX_SPANS:
                    break
                remove = {s["span_id"] for s in traces[tid]}
                self.spans = [s for s in self.spans if s["span_id"] not in remove]
        return len(self.spans) != original

    def _save(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.spans, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def record(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"trace_id", "spans"}:
            return 400, {"error": "bad trace"}
        trace_id = str(payload.get("trace_id") or "")
        rows = payload.get("spans")
        if not TRACE_ID.fullmatch(trace_id) or not isinstance(rows, list) or not rows or len(rows) > MAX_BODY_SPANS:
            return 400, {"error": "bad trace"}
        now, clean = self.clock(), []
        for row in rows:
            span = self._clean_span(trace_id, row, now)
            if span is None:
                return 400, {"error": "bad span"}
            clean.append(span)
        ids = {s["span_id"] for s in clean}
        if len(ids) != len(clean):
            return 400, {"error": "duplicate span"}
        with self.lock:
            parents = {s["span_id"] for s in self.spans if s["trace_id"] == trace_id}
            if any(s["parent_span_id"] and s["parent_span_id"] not in ids | parents for s in clean):
                return 400, {"error": "orphan span"}
            previous = self.spans
            self.spans = list(previous)
            self._prune(now)
            keys = {(s["trace_id"], s["span_id"]): i for i, s in enumerate(self.spans)}
            for span in clean:
                key = (trace_id, span["span_id"])
                if key in keys:
                    self.spans[keys[key]] = span
                else:
                    self.spans.append(span)
            self._prune(now)
            try:
                self._save()
            except OSError:
                self.spans = previous
                return 503, {"error": "trace storage unavailable"}
        return 200, {"ok": True, "accepted": len(clean)}

    def list(self, limit=100):
        now = self.clock()
        with self.lock:
            if self._prune(now):
                try:
                    self._save()
                except OSError:
                    pass
            grouped = {}
            for s in self.spans:
                grouped.setdefault(s["trace_id"], []).append(s)
            out = []
            for trace_id, spans in grouped.items():
                spans.sort(key=lambda s: (s["started_at"], s["span_id"]))
                root = next((s for s in spans if not s["parent_span_id"]), spans[0])
                out.append({"trace_id": trace_id, "started_at": spans[0]["started_at"],
                            "ended_at": max(s["ended_at"] for s in spans), "spans": len(spans),
                            "status": root["status"], "root": root["operation"]})
            out.sort(key=lambda x: x["ended_at"], reverse=True)
            return {"traces": out[:max(1, min(200, int(limit)))], "retention_days": 7}

    def get(self, trace_id):
        if not TRACE_ID.fullmatch(str(trace_id or "")):
            return None
        with self.lock:
            rows = [dict(s) for s in self.spans if s["trace_id"] == trace_id]
        rows.sort(key=lambda s: (s["started_at"], s["span_id"]))
        return {"trace_id": trace_id, "spans": rows} if rows else None
