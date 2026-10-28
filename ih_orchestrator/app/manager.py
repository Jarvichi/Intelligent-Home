"""Add-on manager page (v0.6.0). Ingress only: the server on IH_MANAGER_PORT answers the Supervisor ingress gateway (172.30.32.2) and nobody else.
Lists the add-ons of catalogue.json merged with Supervisor data, runs install/start/stop/restart/update in background jobs, shows needs / needed by.
Acts ONLY on catalogue slugs of our own repository (same scope rule as the provisioner). stdlib only. Never logs or returns option values."""
import json, os, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import provisioner

INGRESS_IP = "172.30.32.2"   # the Supervisor ingress gateway; host_network means the port is open to the LAN, so every other client is refused
ACTIONS = ("install", "start", "stop", "restart", "update")
EXTERNAL = {"mqtt": "core_mosquitto"}   # requires that are not ours: only checked, never acted on
LOG_LINES = 50
JOB_KEEP = 50
SLOW = 900   # seconds: install and update can take minutes (local build)


def client_allowed(ip):
    return ip == INGRESS_IP


def sup_slow(method, path, body=None):
    return provisioner.supervisor(method, path, body, timeout=SLOW)


def sup_text(path, timeout=20):
    """GET a plain-text Supervisor endpoint (logs); returns text. Raises SupError."""
    tok = os.environ.get("SUPERVISOR_TOKEN", "") or provisioner._s6_token()
    req = urllib.request.Request(provisioner.SUP + path, headers={"Authorization": "Bearer " + tok, "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise provisioner.SupError("http_%d" % e.code)
    except Exception as e:
        raise provisioner.SupError(type(e).__name__)


def load_catalogue(path):
    """[{name, slug, description, kind, requires, version}] from catalogue.json; [] if missing or bad."""
    try:
        d = json.load(open(path))
        return [x for x in d if isinstance(x, dict) and isinstance(x.get("slug"), str)] if isinstance(d, list) else []
    except Exception:
        return []


def needs_map(cat):
    """{slug: [required slug]} and the reverse {slug: [slugs that need it]}. Unknown names are kept (e.g. mqtt)."""
    needs = {c["slug"]: [r for r in c.get("requires") or [] if isinstance(r, str)] for c in cat}
    by = {}
    for s, rs in needs.items():
        for r in rs:
            by.setdefault(r, []).append(s)
    return needs, {k: sorted(v) for k, v in by.items()}


def dep_status(req, installed):
    """'ok' | 'stopped' | 'not installed' for one required name. installed: {short: state}."""
    st = installed.get(req)
    return "not installed" if st is None else ("ok" if st == "started" else "stopped")


def install_order(short, needs, installed, seen=None):
    """Required own add-ons that are not installed, dependencies first, then short itself. External names (mqtt) skipped."""
    seen = seen if seen is not None else set()
    out = []
    for r in needs.get(short, []):
        if r in EXTERNAL or r in seen or r in installed or r not in needs:
            continue
        seen.add(r)
        out += install_order(r, needs, installed, seen)
        out.append(r)
    return out


class Jobs:
    """Background runner. States: queued, running, done, error. Keeps the last JOB_KEEP jobs."""
    def __init__(self):
        self.lock, self.jobs, self.n, self.events = threading.Lock(), {}, 0, {}

    def submit(self, label, fn):
        with self.lock:
            self.n += 1
            jid = "j%d" % self.n
            self.jobs[jid] = {"id": jid, "label": label, "state": "queued", "steps": [], "error": None, "started": time.time(), "finished": None}
            self.events[jid] = threading.Event()
            for old in sorted(self.jobs, key=lambda k: int(k[1:]))[:-JOB_KEEP]:
                self.jobs.pop(old, None); self.events.pop(old, None)
        threading.Thread(target=self._run, args=(jid, fn), daemon=True).start()
        return jid

    def _run(self, jid, fn):
        j = self.jobs[jid]
        j["state"] = "running"
        try:
            fn(lambda s: j["steps"].append(str(s)[:120]))
            j["state"] = "done"
        except Exception as e:
            j["state"], j["error"] = "error", str(e) if isinstance(e, provisioner.SupError) else type(e).__name__
        j["finished"] = time.time()
        self.events[jid].set()

    def get(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            return dict(j, steps=list(j["steps"])) if j else None

    def wait(self, jid, timeout=5):
        ev = self.events.get(jid)
        return bool(ev and ev.wait(timeout))


class Manager:
    def __init__(self, catalogue, sup=sup_slow, text=sup_text, prov=lambda: None, seen=lambda: {}, log=print, ttl=60):
        self.catalogue, self.sup, self.text, self.prov, self.seen, self.log, self.ttl = catalogue, sup, text, prov, seen, log, ttl
        self.jobs, self.busy, self.lock = Jobs(), set(), threading.Lock()
        self.cache = (0, None)

    # ---- scope
    def context(self):
        """(prefix, repo) of this add-on. Raises SupError."""
        me = self.sup("GET", "/addons/self/info")
        prefix = provisioner.prefix_from(me.get("slug") if isinstance(me, dict) else "")
        repo = me.get("repository") if isinstance(me, dict) else None
        if not prefix or provisioner.empty(repo):
            raise provisioner.SupError("no_self_slug")
        return prefix, repo

    def scope(self, short):
        """-> (full slug, repo) or raises ValueError. Only catalogue slugs, never the orchestrator itself, and the repository must be ours."""
        names = {c["slug"] for c in self.catalogue}
        if short not in names or short == "ih_orchestrator" or not short.startswith("ih_"):
            raise ValueError("not an add-on of this repository")
        prefix, repo = self.context()
        full = prefix + "_" + short
        try:
            info = self.sup("GET", "/addons/%s/info" % full)
        except provisioner.SupError:
            info = self.sup("GET", "/store/addons/%s" % full)
        if not isinstance(info, dict) or info.get("repository") != repo:
            raise ValueError("not an add-on of this repository")
        return full, repo

    # ---- table
    def installed_map(self, prefix):
        """{short: {state, version, latest, update, repository}} for installed add-ons (+ external names like mqtt)."""
        lst = self.sup("GET", "/addons")
        items = lst.get("addons") if isinstance(lst, dict) else lst
        out = {}
        for x in items if isinstance(items, list) else []:
            if not isinstance(x, dict) or not isinstance(x.get("slug"), str):
                continue
            s = x["slug"]
            short = s[len(prefix) + 1:] if s.startswith(prefix + "_ih_") else next((k for k, v in EXTERNAL.items() if v == s), None)
            if short:
                out[short] = {"state": x.get("state"), "version": x.get("version"), "latest": x.get("version_latest"),
                              "update": bool(x.get("update_available")), "slug": s}
        return out

    def rows(self, force=False):
        t, cached = self.cache
        if cached is not None and not force and time.time() - t < self.ttl:
            return cached
        data = self._rows()
        self.cache = (time.time(), data)
        return data

    def warm(self, every=20, stop=lambda: False, sleep=time.sleep):
        """Background refresher (28 Oct): ~24 sequential stats calls take longer than the page waits,
        so a cold request left the table empty. Keeps the cache fresh; page requests then answer at once."""
        while not stop():
            try:
                self.rows(force=True)
            except Exception as e:
                self.log("manager warm: %s" % type(e).__name__)
            sleep(every)

    def _rows(self):
        prefix, _ = self.context()
        inst = self.installed_map(prefix)
        states = {k: v["state"] for k, v in inst.items()}
        needs, by = needs_map(self.catalogue)
        seen = self.seen() or {}
        settings = ((self.prov().get_status() if self.prov() else None) or {}).get("addons") or {}
        out = []
        for c in self.catalogue:
            s = c["slug"]
            i = inst.get(s)
            r = {"slug": s, "name": c.get("name") or s, "description": str(c.get("description") or "")[:200], "kind": c.get("kind"),
                 "installed": bool(i), "version": i["version"] if i else None, "latest": (i or {}).get("latest") or c.get("version"),
                 "state": i["state"] if i else None, "update": bool(i and i["update"]), "boot": None, "cpu": None, "mem_mb": None,
                 "mem_pct": None, "seen": seen.get(s), "missing_settings": len(settings[s].get("missing") or []) if s in settings else None,
                 "needs": [{"slug": n, "status": dep_status(n, states)} for n in needs.get(s, [])],
                 "needed_by": [{"slug": n, "status": dep_status(n, states)} for n in by.get(s, [])],
                 "can_act": s != "ih_orchestrator"}
            if i:
                self._live(prefix + "_" + s, r)
            out.append(r)
        return out

    def _live(self, full, r):
        try:
            r["boot"] = (self.sup("GET", "/addons/%s/info" % full) or {}).get("boot")
            if r["state"] == "started":
                st = self.sup("GET", "/addons/%s/stats" % full) or {}
                r["cpu"] = st.get("cpu_percent")
                r["mem_mb"] = round(float(st.get("memory_usage") or 0) / 1048576, 1)
                r["mem_pct"] = st.get("memory_percent")
        except (provisioner.SupError, ValueError, TypeError):
            pass

    # ---- actions
    def warn_missing(self, short):
        """List of required names not installed (own ones, plus external like mqtt)."""
        prefix, _ = self.context()
        inst = self.installed_map(prefix)
        needs, _by = needs_map(self.catalogue)
        return [r for r in needs.get(short, []) if r not in inst]

    def act(self, action, short, with_requires=False):
        """-> (http code, object). Starts a background job; install of an add-on with missing requires warns first."""
        if action not in ACTIONS:
            return 400, {"error": "unknown action"}
        try:
            full, _repo = self.scope(short)
        except ValueError as e:
            return 403, {"error": str(e)}
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        order = []
        if action == "install":
            try:
                missing = self.warn_missing(short)
            except provisioner.SupError as e:
                return 502, {"error": str(e)}
            needs, _ = needs_map(self.catalogue)
            own = [m for m in missing if m not in EXTERNAL]
            if missing and not with_requires:
                return 409, {"warning": "Needs first: " + ", ".join(missing), "missing": missing, "installable": own,
                             "external": [m for m in missing if m in EXTERNAL]}
            order = install_order(short, needs, {k: 1 for k in needs if k not in own})
        with self.lock:
            if short in self.busy:
                return 409, {"error": "busy: another action is running for this add-on"}
            self.busy.add(short)
        jid = self.jobs.submit("%s %s" % (action, short), lambda step: self._job(action, short, full, order, step))
        return 202, {"job": jid}

    def _job(self, action, short, full, order, step):
        try:
            prefix, _ = self.context()
            for dep in order:
                step("install " + dep)
                self.sup("POST", "/store/addons/%s_%s/install" % (prefix, dep))
            step("%s %s" % (action, short))
            path = "/store/addons/%s/install" % full if action == "install" else "/addons/%s/%s" % (full, action)
            self.sup("POST", path)
            if action == "install" or order:
                self._provision(step)
        finally:
            with self.lock:
                self.busy.discard(short)
            self.cache = (0, None)

    def _provision(self, step):
        p = self.prov()
        if not p:
            step("provisioner is off: fill the settings by hand"); return
        step("provisioner run")
        p.run()

    def logs(self, short):
        """-> (code, obj) with the last LOG_LINES lines."""
        try:
            full, _ = self.scope(short)
            text = self.text("/addons/%s/logs" % full)
        except ValueError as e:
            return 403, {"error": str(e)}
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        return 200, {"slug": short, "lines": text.splitlines()[-LOG_LINES:]}

    def job(self, jid):
        j = self.jobs.get(jid)
        return (200, j) if j else (404, {"error": "unknown job"})


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Intelligent Home add-ons</title>
<style>body{font:15px system-ui;margin:10px}table{border-collapse:collapse;width:100%}td,th{padding:4px 6px;text-align:left;border-bottom:1px solid #8884;vertical-align:top}
.d{font-size:12px;opacity:.7}.r{color:#c62828;font-weight:bold}.g{color:#2e7d32}.o{color:#ef6c00}button{margin:1px;padding:3px 7px}
pre{background:#8882;padding:6px;overflow:auto;max-height:260px;font-size:12px}#msg{padding:6px;border:1px solid #8886;margin:6px 0;display:none}.w{overflow-x:auto}</style>
<h2>Intelligent Home add-ons</h2><div id=msg></div><div class=w><table id=t><tr><td>Loading the add-on list (the first load after a restart can take about 30 seconds)...</td></tr></table></div><h3>Dependencies</h3><div class=w><table id=dep></table></div><pre id=log hidden></pre>
<script>
var B=location.pathname.endsWith('/')?location.pathname:location.pathname+'/';
function U(p){return B+p}
function E(tag,txt,cls){var e=document.createElement(tag);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e}
function say(t){var m=document.getElementById('msg');m.style.display=t?'block':'none';m.textContent=t||''}
function btn(label,fn){var b=E('button',label);b.onclick=fn;return b}
function cell(tr,x,cls){var td=E('td');if(typeof x==='string'||typeof x==='number')td.textContent=x;else if(x)td.appendChild(x);if(cls)td.className=cls;tr.appendChild(td);return td}
function post(a,s,extra){return fetch(U('api/action/'+a+'/'+s+(extra||'')),{method:'POST'}).then(function(r){return r.json().then(function(j){return[r.status,j]})})}
function act(a,s,extra){say(a+' '+s+' ...');post(a,s,extra).then(function(x){var c=x[0],j=x[1];
 if(c==202)return poll(j.job);
 if(c==409&&j.warning){if(j.installable&&j.installable.length&&confirm(j.warning+'\\nInstall those first?'))return act(a,s,'?with_requires=1');say(j.warning+(j.external&&j.external.length?' ('+j.external.join(', ')+' is not ours: install it from the add-on store)':''));return}
 say(j.error||j.warning||('failed '+c))}).catch(function(e){say('error: '+e)})}
function poll(id){fetch(U('api/job/'+id)).then(function(r){return r.json()}).then(function(j){
 say(j.label+': '+j.state+(j.steps.length?' ('+j.steps[j.steps.length-1]+')':'')+(j.error?' ERROR '+j.error:''));
 if(j.state=='queued'||j.state=='running')setTimeout(function(){poll(id)},2000);else load()})}
function logs(s){fetch(U('api/logs/'+s)).then(function(r){return r.json()}).then(function(j){var p=document.getElementById('log');p.hidden=false;p.textContent=j.error?j.error:s+'\\n'+j.lines.join('\\n')})}
function age(x){if(!x||x.age_s==null)return '-';var a=x.age_s;return (x.state||'')+' '+(a<90?a+'s':a<5400?Math.round(a/60)+'m':Math.round(a/3600)+'h')}
function load(){fetch(U('api/addons')).then(function(r){return r.json()}).then(function(d){
 if(d.error){say(d.error);return}
 var t=document.getElementById('t');t.textContent='';var h=E('tr');['Add-on','Version','State','Boot','CPU %','Mem MB','Seen','Missing','Actions'].forEach(function(x){h.appendChild(E('th',x))});t.appendChild(h);
 d.addons.forEach(function(a){var tr=E('tr'),n=E('div');n.appendChild(E('b',a.slug));n.appendChild(E('div',a.description,'d'));cell(tr,n);
  cell(tr,a.installed?(a.version||'?')+(a.update?' -> '+a.latest:''):'not installed ('+(a.latest||'?')+')',a.update?'o':'');
  cell(tr,a.state||'-',a.state=='started'?'g':(a.installed?'r':''));cell(tr,a.boot||'-');cell(tr,a.cpu==null?'-':a.cpu);cell(tr,a.mem_mb==null?'-':a.mem_mb);
  cell(tr,age(a.seen));cell(tr,a.missing_settings==null?'-':a.missing_settings,a.missing_settings?'o':'');
  var b=E('div');if(a.can_act){if(!a.installed)b.appendChild(btn('Install',function(){act('install',a.slug)}));else{
   b.appendChild(btn('Start',function(){act('start',a.slug)}));b.appendChild(btn('Stop',function(){act('stop',a.slug)}));b.appendChild(btn('Restart',function(){act('restart',a.slug)}));
   if(a.update)b.appendChild(btn('Update',function(){act('update',a.slug)}));b.appendChild(btn('Logs',function(){logs(a.slug)}))}}
  cell(tr,b);t.appendChild(tr)});
 var p=document.getElementById('dep');p.textContent='';var h2=E('tr');['Add-on','Needs','Needed by'].forEach(function(x){h2.appendChild(E('th',x))});p.appendChild(h2);
 function list(xs){var s=E('div');xs.forEach(function(x){s.appendChild(E('div',x.slug+(x.status=='ok'?'':' ('+x.status+')'),x.status=='ok'?'g':'r'))});return s}
 d.addons.forEach(function(a){if(!a.needs.length&&!a.needed_by.length)return;var tr=E('tr');cell(tr,a.slug);cell(tr,list(a.needs));cell(tr,list(a.needed_by));p.appendChild(tr)})
 }).catch(function(e){say('error: '+e)})}
load();setInterval(function(){if(!document.hidden)load()},30000);
</script>"""


def make_handler(mgr):
    class MH(BaseHTTPRequestHandler):
        def _send(self, code, body, ct):
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _gate(self):
            if not client_allowed(self.client_address[0]):
                self._json(403, {"error": "ingress only"}); return False
            return True

        def do_GET(self):
            if not self._gate():
                return
            p = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if p == "/":
                    return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                if p == "/api/addons":
                    return self._json(200, {"addons": mgr.rows()})
                if p.startswith("/api/job/"):
                    return self._json(*mgr.job(p[9:]))
                if p.startswith("/api/logs/"):
                    return self._json(*mgr.logs(p[10:]))
            except provisioner.SupError as e:
                return self._json(502, {"error": str(e)})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._gate():
                return
            u = urlparse(self.path)
            parts = u.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "action"]:
                wr = parse_qs(u.query).get("with_requires", [""])[0] == "1"
                return self._json(*mgr.act(parts[2], parts[3], wr))
            self._json(404, {"error": "not found"})

        def log_message(self, *a):
            pass
    return MH


def serve(mgr, port):
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(mgr)).serve_forever()
