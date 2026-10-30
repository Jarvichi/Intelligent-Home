"""Plugin platform for ih_jobs 0.9.0: auto-discovery of plugins/*.py, per-plugin dry/live, state under /data/<plugin>/, a simple built-in timer.
Schedule kinds: daily {at}, every {minutes}, service {seconds}, event (0.12.0: no timer; the plugin's event(ctx, payload) is called by POST /plugin/<name>/event): the host runs the plugin's run(ctx) in its OWN supervised thread every `seconds` (fresh ctx each time, so mode, kill switches and the clock are re-read; a crash never stops the loop).
A plugin runs only when it is listed in the option `plugins`. It is LIVE only when plugins[].live is true AND /data/STOP_LIVE and /data/STOP_<name> do not exist.
Nothing here ever prints option values."""
import importlib, json, os, pkgutil, threading, time, traceback
from datetime import datetime, timedelta, timezone

import ihlib.ha, ihlib.discord, ihlib.http, ihlib.state, ihlib.log

CATCHUP_MIN = 60   # a daily plugin missed by less than this still runs (add-on started late)
STOP_ALL = 'STOP_LIVE'


def discover(package='plugins'):
    """{name: module} for every plugins/*.py that has NAME, SCHEDULE, DEFAULTS and run."""
    pkg = importlib.import_module(package)
    out = {}
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith('_'):
            continue
        mod = importlib.import_module('%s.%s' % (package, m.name))
        if all(hasattr(mod, a) for a in ('NAME', 'SCHEDULE', 'DEFAULTS', 'run')):
            out[mod.NAME] = mod
    return out


def parse_plugins_option(raw):
    """-> list of {name, live, settings(str)}. Bad rows dropped."""
    out = []
    for r in (raw or []):
        if isinstance(r, dict) and str(r.get('name') or '').strip():
            out.append({'name': str(r['name']).strip(), 'live': r.get('live') is True, 'settings': str(r.get('settings') or '').strip()})
    return out


def get_tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _hm(text):
    h, m = str(text).split(':')
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError('bad time')
    return h, m


class Discord:
    """ctx.discord: dry-aware sender. The token comes from the option named by the caller; never logged."""
    def __init__(self, opts, dry, log, opener=None):
        self.opts, self.dry, self.log, self.opener = opts, dry, log, opener

    def dm(self, user_id, text, token_option='discord_token', embeds=None, file=None):
        """True if really sent; False in dry (message text is logged). embeds = list of dicts; file = (filename, bytes), shown in an embed as attachment://<filename>."""
        if self.dry:
            extra = (' [+%d embed]' % len(embeds) if embeds else '') + (' [+file %s]' % file[0] if file else '')
            self.log('[DRY] would DM the configured user: ' + ihlib.log.one_line(text, 400) + extra)
            if embeds:
                self.log('[DRY] embed: ' + ihlib.log.one_line(json.dumps(embeds, ensure_ascii=False), 700))
            return False
        if embeds or file:
            ihlib.discord.send_dm_rich(self.opts.get(token_option) or '', user_id, text, embeds, file, self.opener)
        else:
            ihlib.discord.send_dm(self.opts.get(token_option) or '', user_id, text, self.opener)
        return True

    def post(self, channel_id, text, token_option='discord_token'):
        if self.dry:
            self.log('[DRY] would post to the configured channel: ' + ihlib.log.one_line(text, 400))
            return False
        ihlib.discord.post(self.opts.get(token_option) or '', channel_id, text, self.opener)
        return True


class PluginCtx:
    def __init__(self, name, settings, dry, now, ha, discord, state, log, http=None, data=None, opts=None):
        self.name, self.settings, self.dry, self.now = name, settings, dry, now
        self.ha, self.discord, self.state, self.log = ha, discord, state, log
        self.http = http or ihlib.http.Http()   # ctx.http.get_json(url) / get_bytes(url): outside web, read-only, works in dry too
        self.data = data   # the add-on data folder (plugins may READ another plugin's folder under it)
        self._opts = opts or {}

    def option(self, name):
        """Value of an add-on option as text ('' if unset). For keys and tokens a plugin needs; never log the result."""
        v = self._opts.get(name)
        return '' if v is None else str(v)

    def sibling_state(self, plugin):
        """Read-only view of another plugin's state store (same /data root)."""
        return ihlib.state.Store(os.path.join(self.data, plugin)) if self.data else ihlib.state.Store(os.path.join(os.path.dirname(self.state.folder), plugin))

    def service(self, domain, service, data=None):
        """Dry-aware service call."""
        if self.dry:
            self.log('[DRY] would call %s.%s' % (domain, service))
            return None
        return self.ha.call_service(domain, service, data)


class Host:
    def __init__(self, opts, data='/data', log=print, token=None, opener=None, connect=None, plugins=None, clock=None):
        self.opts, self.data, self.log_sink = opts, data, log
        self.mods = plugins if plugins is not None else discover()
        self.cfg = {r['name']: r for r in parse_plugins_option(opts.get('plugins'))}
        self.secrets = ihlib.log.secret_values(opts)
        self.tz = get_tz(opts.get('timezone') or 'Europe/London')
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.ha = ihlib.ha.HA(token=token, opener=opener, connect=connect)
        self.opener = opener
        self.locks = {n: threading.Lock() for n in self.mods}
        self.info = {n: {'last_run': None, 'last_ok': None, 'last_error': None, 'last_mode': None, 'runs': 0, 'errors': 0} for n in self.mods}
        self.thread = None
        self.stop_evt = threading.Event()
        self.service_threads = {}

    # ---- config ----
    def settings(self, name):
        """(settings dict, error text). DEFAULTS overlaid by the settings JSON."""
        s = dict(self.mods[name].DEFAULTS)
        raw = (self.cfg.get(name) or {}).get('settings') or ''
        if raw:
            try:
                o = json.loads(raw)
                if not isinstance(o, dict):
                    raise ValueError
                s.update(o)
            except ValueError:
                return s, 'settings is not a JSON object'
        return s, None

    def mode(self, name):
        c = self.cfg.get(name)
        if not c:
            return 'off', 'not listed in plugins option'
        if not c['live']:
            return 'dry', 'live is false'
        for stop in (STOP_ALL, 'STOP_' + name):
            if os.path.exists(os.path.join(self.data, stop)):
                return 'dry', 'kill switch file %s exists' % stop
        return 'live', 'live'

    def schedule(self, name):
        sch = dict(self.mods[name].SCHEDULE)
        s, _ = self.settings(name)
        if sch.get('kind') == 'daily' and isinstance(s.get('at'), str):
            sch['at'] = s['at']
        if sch.get('kind') == 'every' and isinstance(s.get('minutes'), int) and s['minutes'] > 0:
            sch['minutes'] = s['minutes']
        if sch.get('kind') == 'service' and isinstance(s.get('seconds'), int) and s['seconds'] > 0:
            sch['seconds'] = s['seconds']
        return sch

    # ---- running ----
    def _plog(self, name):
        return ihlib.log.Logger(self.log_sink, self.secrets, prefix=name)

    def run(self, name, trigger='manual'):
        """Run one plugin now. -> True/False. Never raises."""
        if name not in self.mods or name not in self.cfg:
            return False
        with self.locks[name]:
            mode, _ = self.mode(name)
            info, log = self.info[name], self._plog(name)
            info['last_mode'] = mode
            info['last_run'] = self.clock().isoformat(timespec='seconds')
            settings, err = self.settings(name)
            if err:
                info['last_ok'], info['last_error'] = False, err
                info['errors'] += 1
                log('error: ' + err)
                return False
            dry = mode != 'live'
            ctx = PluginCtx(name, settings, dry, self.clock(), self.ha, Discord(self.opts, dry, log, self.opener),
                            ihlib.state.Store(os.path.join(self.data, name)), log, ihlib.http.Http(self.opener), self.data, self.opts)
            try:
                ok = self.mods[name].run(ctx) is not False
                info['runs'] += 1
                info['last_ok'] = ok
                info['last_error'] = None if ok else 'plugin returned false'
                return ok
            except Exception as e:
                info['errors'] += 1
                info['last_ok'] = False
                info['last_error'] = ihlib.log.scrub(type(e).__name__ + ': ' + traceback.format_exc(limit=2).strip().splitlines()[-1][:160], self.secrets)
                log('error: ' + info['last_error'])
                return False

    def event(self, name, payload, trigger='event'):
        """0.12.0: hand one event (a JSON object) to a plugin that has event(ctx, payload). -> (ok, why). Never raises. Same dry/live and kill-switch rules as run()."""
        if name not in self.mods or name not in self.cfg:
            return False, 'plugin not configured'
        if not hasattr(self.mods[name], 'event'):
            return False, 'plugin takes no events'
        with self.locks[name]:
            mode, _ = self.mode(name)
            info, log = self.info[name], self._plog(name)
            info['last_mode'] = mode
            info['last_run'] = self.clock().isoformat(timespec='seconds')
            settings, err = self.settings(name)
            if err:
                info['last_ok'], info['last_error'] = False, err
                info['errors'] += 1
                log('error: ' + err)
                return False, err
            dry = mode != 'live'
            ctx = PluginCtx(name, settings, dry, self.clock(), self.ha, Discord(self.opts, dry, log, self.opener),
                            ihlib.state.Store(os.path.join(self.data, name)), log, ihlib.http.Http(self.opener), self.data, self.opts)
            try:
                ok = self.mods[name].event(ctx, payload) is not False
                info['runs'] += 1
                info['last_ok'] = ok
                info['last_error'] = None if ok else 'plugin returned false'
                return ok, 'ok' if ok else 'plugin returned false'
            except Exception as e:
                info['errors'] += 1
                info['last_ok'] = False
                info['last_error'] = ihlib.log.scrub(type(e).__name__ + ': ' + traceback.format_exc(limit=2).strip().splitlines()[-1][:160], self.secrets)
                log('error: ' + info['last_error'])
                return False, 'plugin error'

    # ---- timing ----
    def next_run(self, name, now=None):
        """Datetime of the next scheduled run, or None."""
        now = now or self.clock()
        sch = self.schedule(name)
        try:
            if sch['kind'] == 'daily':
                h, m = _hm(sch['at'])
                t = now.replace(hour=h, minute=m, second=0, microsecond=0)
                return t if t > now else t + timedelta(days=1)
            if sch['kind'] == 'every':
                last = self.info[name].get('last_ts')
                return datetime.fromtimestamp(last + int(sch['minutes']) * 60, self.tz) if last else now
            if sch['kind'] == 'service':
                last = self.info[name].get('last_ts')
                return datetime.fromtimestamp(last + int(sch['seconds']), self.tz) if last else now
        except (KeyError, ValueError, TypeError):
            return None
        return None

    def due(self, name, now=None):
        """True when a scheduled run is due now (and has not happened for this slot)."""
        now = now or self.clock()
        sch, inf = self.schedule(name), self.info[name]
        try:
            if sch['kind'] == 'daily':
                h, m = _hm(sch['at'])
                slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
                return slot <= now < slot + timedelta(minutes=CATCHUP_MIN) and inf.get('slot_done') != slot.isoformat()
            if sch['kind'] == 'every':
                last = inf.get('last_ts')
                return last is None or time.time() - last >= int(sch['minutes']) * 60
        except (KeyError, ValueError, TypeError):
            return False
        return False

    def tick(self, now=None):
        """Run every configured plugin that is due. Returns the names run."""
        ran, now = [], now or self.clock()
        for name in sorted(self.mods):
            if name in self.cfg and self.due(name, now):
                sch = self.schedule(name)
                if sch['kind'] == 'daily':
                    h, m = _hm(sch['at'])
                    self.info[name]['slot_done'] = now.replace(hour=h, minute=m, second=0, microsecond=0).isoformat()
                self.info[name]['last_ts'] = time.time()
                self.run(name, 'timer')
                ran.append(name)
        return ran

    def start(self, interval=30):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception:
                    pass
                time.sleep(interval)
        self.thread = threading.Thread(target=loop, daemon=True, name='plugins')
        self.thread.start()
        self.start_services()

    def service_names(self):
        return [n for n in sorted(self.mods) if n in self.cfg and self.mods[n].SCHEDULE.get('kind') == 'service']

    def service_loop(self, name):
        """Supervised loop of one `service` plugin: run, wait `seconds`, repeat. Re-reads the schedule each time; ends on stop()."""
        while not self.stop_evt.is_set():
            try:
                self.info[name]['last_ts'] = time.time()
                self.run(name, 'service')
            except Exception:
                pass
            try:
                wait = max(1, int(self.schedule(name).get('seconds', 60)))
            except (TypeError, ValueError):
                wait = 60
            self.stop_evt.wait(wait)

    def start_services(self):
        for n in self.service_names():
            if n not in self.service_threads or not self.service_threads[n].is_alive():
                t = threading.Thread(target=self.service_loop, args=(n,), daemon=True, name='plugin-' + n)
                self.service_threads[n] = t
                t.start()

    def stop(self):
        self.stop_evt.set()

    def status(self):
        out = {}
        for name in sorted(self.mods):
            mode, why = self.mode(name)
            i = self.info[name]
            nxt = self.next_run(name) if name in self.cfg else None
            out[name] = {'mode': mode, 'why': why, 'schedule': self.schedule(name), 'runs': i['runs'], 'errors': i['errors'], 'last_run': i['last_run'],
                         'last_ok': i['last_ok'], 'last_mode': i['last_mode'], 'last_error': i['last_error'],
                         'next_run': nxt.isoformat(timespec='seconds') if nxt else None}
        for name in self.cfg:
            if name not in self.mods:
                out[name] = {'mode': 'off', 'why': 'unknown plugin name'}
        return out
