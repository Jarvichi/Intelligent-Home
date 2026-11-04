"""Validate the small, read-only status panels pushed by IH add-ons.

Only this bounded v1 shape may reach the Manager. It contains no HTML, URLs,
options, identifiers, or arbitrary payload fields.
"""
import math
import re

SOURCE = re.compile(r"ih_[a-z0-9_]{1,35}\Z")
LABEL = re.compile(r"[A-Za-z0-9 ._/-]{1,40}\Z")
STATES = {"ok", "attention", "unknown"}


def _text(value, limit):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("bad contribution text")
    if any(ord(c) < 32 or c in "<>" for c in value):
        raise ValueError("bad contribution text")
    return value


def validate(raw, source):
    """Return an allow-listed v1 record or raise ValueError; never echo input."""
    if not SOURCE.fullmatch(source or "") or not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("bad contribution version or source")
    state = raw.get("state")
    if state not in STATES:
        raise ValueError("bad contribution state")
    metrics = raw.get("metrics", [])
    if not isinstance(metrics, list) or len(metrics) > 4:
        raise ValueError("bad contribution metrics")
    clean = []
    for row in metrics:
        if not isinstance(row, dict) or not isinstance(row.get("label"), str) or not LABEL.fullmatch(row["label"]):
            raise ValueError("bad contribution metric")
        value = row.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1_000_000_000:
            raise ValueError("bad contribution metric")
        clean.append({"label": row["label"], "value": value})
    activity = raw.get("last_activity_at")
    if activity is not None and (isinstance(activity, bool) or not isinstance(activity, (int, float)) or not math.isfinite(activity) or activity < 0):
        raise ValueError("bad contribution activity")
    return {"source": source, "version": 1, "state": state,
            "summary": _text(raw.get("summary"), 180),
            "next_step": _text(raw.get("next_step"), 180),
            "last_activity_at": activity, "metrics": clean}
