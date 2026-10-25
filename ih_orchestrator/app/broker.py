"""Minimal secrets broker for the Intelligent Home orchestrator. stdlib only, NO outbound network calls.
Secrets live in memory only. The audit log holds names, module, time, result: never values.
Approval design: see admin_approve() and the brain note projects/intelligent-home-architecture."""
import hashlib, hmac, ipaddress, json, os, re, secrets as _rnd, threading, time

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
TOKEN_TTL = 3600
RATE_MAX, RATE_WINDOW = 10, 60
ADMIN_MAX_FAILS, ADMIN_LOCK = 5, 300


def parse_secrets(path):
    """Tiny YAML subset: top-level 'key: value' lines only. Quotes stripped. Comments and indented lines skipped."""
    out = {}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for ln in lines:
        if not ln or ln[0] in " \t#-" or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        if NAME_RE.match(k) and v != "":
            out[k] = v
    return out


def is_local(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    return a.is_loopback or a.is_private


class Broker:
    def __init__(self, secrets_path="/data/secrets.yaml", data_dir="/data", clock=time.time, log=print):
        self.secrets_path, self.data = secrets_path, data_dir
        self.approvals_path = os.path.join(data_dir, "approved_secrets.json")
        self.pin_path = os.path.join(data_dir, "admin_pin.json")
        self.audit_path = os.path.join(data_dir, "audit.log")
        self.clock, self.log = clock, log
        self.lock = threading.RLock()
        self.tokens = {}      # token -> (module, expires)  (memory only)
        self.hits = {}        # module -> [timestamps]
        self.fails, self.locked_until = 0, 0
        self.code = None
        os.makedirs(data_dir, exist_ok=True)
        self.new_code()

    # ---- approvals file: {"allowed_modules": [...], "approved": {module: [names]}} ----
    def _load(self):
        try:
            d = json.load(open(self.approvals_path))
        except (OSError, ValueError):
            d = {}
        return {"allowed_modules": list(d.get("allowed_modules", [])), "approved": dict(d.get("approved", {})),
                "global_secrets": list(d.get("global_secrets", []))}

    def _save(self, d):
        tmp = self.approvals_path + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1)
        os.replace(tmp, self.approvals_path)

    # ---- audit: append-only, no values ----
    def audit(self, module, event, result, names=()):
        safe = lambda s: re.sub(r"[^A-Za-z0-9_.\-,]", "?", str(s))[:200]
        line = "%s\t%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.clock())),
                                         safe(module), safe(event), safe(result), safe(",".join(names)))
        with open(self.audit_path, "a") as f:
            f.write(line)

    def audit_tail(self, n=100):
        try:
            return open(self.audit_path).read().splitlines()[-n:]
        except OSError:
            return []

    def digest(self, days=7):
        """Names first requested (by a module) within the last N days."""
        first = {}
        for ln in self.audit_tail(10 ** 9):
            p = ln.split("\t")
            if len(p) < 5 or p[2] != "register" or not p[4]:
                continue
            try:
                t = time.mktime(time.strptime(p[0], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            for n in p[4].split(","):
                first.setdefault((p[1], n), t)
        cut = self.clock() - days * 86400
        return sorted("%s:%s" % k for k, t in first.items() if t >= cut)

    # ---- rate limit ----
    def _rate_ok(self, module):
        now = self.clock()
        if module not in self.hits and len(self.hits) > 500:
            self.hits.clear()
        h = [t for t in self.hits.get(module, []) if now - t < RATE_WINDOW]
        if len(h) >= RATE_MAX:
            self.hits[module] = h
            return False
        h.append(now)
        self.hits[module] = h
        return True

    # ---- module side ----
    def register(self, manifest):
        """Returns (http_status, body). Never puts a value in the body for an unapproved name."""
        with self.lock:
            module = manifest.get("name") if isinstance(manifest, dict) else None
            if not isinstance(module, str) or not NAME_RE.match(module):
                self.audit("?", "register", "bad_manifest")
                return 400, {"error": "bad manifest"}
            if not self._rate_ok(module):
                self.audit(module, "register", "rate_limited")
                return 429, {"error": "rate limited"}
            decl = manifest.get("declares_secrets", [])
            if not isinstance(decl, list) or not all(isinstance(n, str) and NAME_RE.match(n) for n in decl):
                self.audit(module, "register", "bad_secret_names")
                return 400, {"error": "bad declares_secrets"}
            d = self._load()
            if module not in d["allowed_modules"]:
                self.audit(module, "register", "refused_unknown_module", decl)
                return 403, {"error": "unknown module"}
            # only names the owner listed as GLOBAL are ever served; per-add-on secrets belong in that add-on's own options
            ok_names = set(d["approved"].get(module, [])) & set(d["global_secrets"])
            have = parse_secrets(self.secrets_path)
            given, pending, missing = {}, [], []
            for n in decl:
                if n not in ok_names:
                    pending.append(n)
                elif n not in have:
                    missing.append(n)
                else:
                    given[n] = have[n]
            tok = _rnd.token_urlsafe(32)
            self.tokens[tok] = (module, self.clock() + TOKEN_TTL)
            self.audit(module, "register", "ok", decl)
            if given:
                self.audit(module, "handover", "ok", sorted(given))
            if pending:
                self.audit(module, "pending", "awaiting_approval", pending)
            return 200, {"token": tok, "expires_in": TOKEN_TTL, "secrets": given, "pending": pending, "missing": missing}

    def heartbeat(self, module, token):
        with self.lock:
            now = self.clock()
            for t in [t for t, (_, e) in self.tokens.items() if e < now]:
                del self.tokens[t]
            ent = self.tokens.get(token) if isinstance(token, str) else None
            if not ent or ent[0] != module:
                self.audit(module if isinstance(module, str) else "?", "heartbeat", "bad_or_expired_token")
                return 401, {"error": "bad or expired token"}
            self.tokens[token] = (module, now + TOKEN_TTL)
            return 200, {"ok": True, "expires_in": TOKEN_TTL}

    # ---- admin side (the owner only) ----
    # CHOICE: three things are needed together: (1) client IP is private/loopback, (2) the one-time code printed in the
    # add-on log at start (single use: a new code is printed after each success or after 5 failures, then 5 min lockout),
    # (3) the owner's PIN, set on the first approval (stored only as a scrypt hash in /data). The PIN is the real barrier
    # against an AI agent or other add-on: anything that holds a Supervisor token can read this add-on's log (so can read the code) and shares the
    # LAN, so (1) and (2) alone do NOT keep an AI agent out.
    def new_code(self):
        self.code = "%08d" % _rnd.randbelow(10 ** 8)
        self.log("IH broker approval code (one time): %s" % self.code)   # the only place a code is ever shown

    def _pin_check(self, pin, new_pin):
        try:
            rec = json.load(open(self.pin_path))
        except (OSError, ValueError):
            rec = None
        if rec is None:
            if not isinstance(new_pin, str) or len(new_pin) < 8:
                return False, "first use: send new_pin (at least 8 characters)"
            salt = _rnd.token_bytes(16)
            h = hashlib.scrypt(new_pin.encode(), salt=salt, n=2 ** 14, r=8, p=1)
            json.dump({"salt": salt.hex(), "hash": h.hex()}, open(self.pin_path, "w"))
            return True, "pin set"
        if not isinstance(pin, str):
            return False, "pin required"
        h = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(rec["salt"]), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(h.hex(), rec["hash"]), "bad pin"

    def admin(self, client_ip, req):
        """req: {action: allow|approve|revoke|global_add|global_remove, module, names?, code, pin|new_pin}"""
        with self.lock:
            if not is_local(client_ip):
                self.audit("admin", "admin", "refused_not_local")
                return 403, {"error": "local only"}
            if self.clock() < self.locked_until:
                return 429, {"error": "locked"}
            ok_code = isinstance(req.get("code"), str) and hmac.compare_digest(req["code"], self.code)
            ok_pin, why = (False, "bad code")
            if ok_code:
                ok_pin, why = self._pin_check(req.get("pin"), req.get("new_pin"))
            if not (ok_code and ok_pin):
                self.fails += 1
                self.audit("admin", "admin", "denied")
                if self.fails >= ADMIN_MAX_FAILS:
                    self.fails, self.locked_until = 0, self.clock() + ADMIN_LOCK
                    self.new_code()
                    self.audit("admin", "admin", "locked_code_rotated")
                return 403, {"error": why}
            module, names = req.get("module"), req.get("names", [])
            if req.get("action") in ("global_add", "global_remove") and module is None:
                module = "global"
            if not isinstance(module, str) or not NAME_RE.match(module) or not isinstance(names, list) \
                    or not all(isinstance(n, str) and NAME_RE.match(n) for n in names):
                return 400, {"error": "bad module or names"}
            d, act = self._load(), req.get("action")
            if act == "allow":
                if module not in d["allowed_modules"]:
                    d["allowed_modules"].append(module)
            elif act == "global_add":
                d["global_secrets"] = sorted(set(d["global_secrets"]) | set(names))
            elif act == "global_remove":
                d["global_secrets"] = [n for n in d["global_secrets"] if n not in names]
            elif act == "approve":
                if module not in d["allowed_modules"]:
                    return 400, {"error": "allow the module first"}
                d["approved"][module] = sorted(set(d["approved"].get(module, [])) | set(names))
            elif act == "revoke":
                d["approved"][module] = [n for n in d["approved"].get(module, []) if n not in names]
                if not names:
                    d["allowed_modules"] = [m for m in d["allowed_modules"] if m != module]
                    d["approved"].pop(module, None)
                    self.tokens = {t: v for t, v in self.tokens.items() if v[0] != module}
            else:
                return 400, {"error": "unknown action"}
            self._save(d)
            self.fails = 0
            self.audit(module, "admin_" + act, "ok", names)
            self.new_code()
            return 200, {"ok": True}
