"""Setup wizards for ih_* add-ons (v1). An add-on opts in by a definition file app/wizards/<short slug>.json:
{"version":1,"title":..,"enable_option":"providers_enabled"|null,
 "groups":[{"id","label","help","enable_values":["steam_library",..],"fields":[{"option","label","kind":"secret|text"}]}]}
The wizard may only touch options NAMED in the definition (and the enable option), only if the add-on's Supervisor
schema has them as text options. GET never returns values (only which fields are set). Values are never logged.
stdlib only; the Supervisor is reached through the injected `sup` callable (tests use a fake)."""
import json, re
from pathlib import Path

import provisioner

WIZ_DIR = Path(__file__).parent / "wizards"
IDENT = re.compile(r"[a-z][a-z0-9_]{0,59}\Z")
SHORT = re.compile(r"ih_[a-z0-9_]{1,35}\Z")
LABEL = re.compile(r"[A-Za-z0-9 ._/()&',:;!?-]+\Z")
KINDS = ("secret", "text")
MAX_VALUE = 2000


def _label(v, limit=80):
    if not isinstance(v, str) or not v or len(v) > limit or not LABEL.fullmatch(v):
        raise ValueError("bad wizard text")
    return v


def validate(raw):
    """Return a clean definition or raise ValueError (never echoes input)."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("bad wizard version")
    enable = raw.get("enable_option")
    if enable is not None and not (isinstance(enable, str) and IDENT.fullmatch(enable)):
        raise ValueError("bad enable option")
    groups, ids, seen = [], set(), set()
    raw_groups = raw.get("groups")
    if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= 12:
        raise ValueError("bad wizard groups")
    for g in raw_groups:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str) or not IDENT.fullmatch(g["id"]) or g["id"] in ids:
            raise ValueError("bad wizard group")
        ids.add(g["id"])
        ev = g.get("enable_values", [])
        if not isinstance(ev, list) or len(ev) > 10 or not all(isinstance(x, str) and IDENT.fullmatch(x) for x in ev):
            raise ValueError("bad enable values")
        if ev and enable is None:
            raise ValueError("enable values without enable option")
        fields = []
        raw_fields = g.get("fields")
        if not isinstance(raw_fields, list) or len(raw_fields) > 10 or not (raw_fields or ev):
            raise ValueError("bad wizard fields")   # a service with no keys (e.g. Xbox via Home Assistant) must at least enable providers
        for f in raw_fields:
            if not isinstance(f, dict) or not isinstance(f.get("option"), str) or not IDENT.fullmatch(f["option"]) \
                    or f["option"] in seen or f.get("kind") not in KINDS:
                raise ValueError("bad wizard field")
            seen.add(f["option"])
            fields.append({"option": f["option"], "label": _label(f.get("label")), "kind": f["kind"]})
        groups.append({"id": g["id"], "label": _label(g.get("label")), "help": _label(g["help"], 180) if g.get("help") else "",
                       "enable_values": ev, "fields": fields})
    return {"version": 1, "title": _label(raw.get("title")), "enable_option": enable, "groups": groups}


def load(short, directory=None):
    """Definition for an add-on short slug, or None when it has not opted in. Raises ValueError when the file is bad."""
    if not SHORT.fullmatch(short or ""):
        return None
    p = Path(directory or WIZ_DIR) / (short + ".json")
    if not p.is_file():
        return None
    return validate(json.loads(p.read_text(encoding="utf-8")))


def available(directory=None):
    out = []
    for p in sorted(Path(directory or WIZ_DIR).glob("ih_*.json")):
        try:
            if SHORT.fullmatch(p.stem):
                validate(json.loads(p.read_text(encoding="utf-8")))
                out.append(p.stem)
        except (ValueError, OSError):
            pass
    return out


def _types(info):
    return provisioner.schema_types(info.get("schema"))


def _enabled(opts, enable):
    v = opts.get(enable) if enable else None
    return {str(x) for x in v} if isinstance(v, list) else set()


def state(defn, info):
    """What the page needs: groups, per field only `set` (bool), per group `enabled`. NEVER values."""
    opts = info.get("options") if isinstance(info.get("options"), dict) else {}
    types = _types(info)
    on = _enabled(opts, defn["enable_option"])
    groups, any_set = [], False
    for g in defn["groups"]:
        fields = []
        for f in g["fields"]:
            ok = provisioner.is_text(types.get(f["option"], ""))
            is_set = ok and not provisioner.empty(opts.get(f["option"]))
            any_set = any_set or is_set
            fields.append({"option": f["option"], "label": f["label"], "kind": f["kind"], "set": is_set, "supported": ok})
        groups.append({"id": g["id"], "label": g["label"], "help": g["help"], "fields": fields,
                       "enabled": bool(set(g["enable_values"]) & on) if g["enable_values"] else None,
                       "complete": all(x["set"] for x in fields)})
    return {"title": defn["title"], "groups": groups, "first_run": not any_set and not on}


def apply(defn, info, answers):
    """answers: {"groups": {group_id: {"enabled": bool, "values": {option: str}}}}. Returns (new full options, changed names).
    Blank values keep the stored value. Raises ValueError (message holds no values)."""
    if not isinstance(answers, dict) or not isinstance(answers.get("groups"), dict):
        raise ValueError("bad answers")
    cur = dict(info.get("options") if isinstance(info.get("options"), dict) else {})
    types = _types(info)
    by_id = {g["id"]: g for g in defn["groups"]}
    changed, enable = [], defn["enable_option"]
    on = _enabled(cur, enable)
    for gid, a in answers["groups"].items():
        g = by_id.get(gid)
        if g is None or not isinstance(a, dict):
            raise ValueError("unknown group")
        allowed = {f["option"] for f in g["fields"]}
        vals = a.get("values", {})
        if not isinstance(vals, dict):
            raise ValueError("bad values")
        for opt, v in vals.items():
            if opt not in allowed:
                raise ValueError("option not in wizard")
            if not isinstance(v, str) or len(v) > MAX_VALUE or any(ord(c) < 32 for c in v):
                raise ValueError("bad value")
            if v.strip() == "":
                continue
            if not provisioner.is_text(types.get(opt, "")):
                raise ValueError("option not a text option")
            if cur.get(opt) != v.strip():
                cur[opt] = v.strip(); changed.append(opt)
        if g["enable_values"] and isinstance(a.get("enabled"), bool):
            for v in g["enable_values"]:
                (on.add if a["enabled"] else on.discard)(v)
    if enable and any(g["enable_values"] for g in defn["groups"]):
        want = sorted(on)
        if want != sorted(_enabled(info.get("options") if isinstance(info.get("options"), dict) else {}, enable)):
            cur[enable] = want; changed.append(enable)
    return cur, sorted(set(changed))
