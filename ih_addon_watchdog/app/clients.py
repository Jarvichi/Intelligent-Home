"""Real clients: Supervisor API, HTTP probe, Tasks add-on, Discord, media probe. stdlib only."""
import json, os, struct, time, urllib.request, urllib.error, wave

SUP = 'http://supervisor'


def _req(method, url, token=None, body=None, timeout=15, raw=False):
    h = {'Content-Type': 'application/json'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        data = resp.read()
    return data.decode('utf-8', 'replace') if raw else (json.loads(data) if data else {})


class Supervisor:
    def __init__(self, token=None):
        self.token = token or os.environ.get('SUPERVISOR_TOKEN', '')

    def addons(self):
        return _req('GET', SUP + '/addons', self.token)['data']['addons']

    def info(self, slug):
        return _req('GET', '%s/addons/%s/info' % (SUP, slug), self.token)['data']

    def stats(self, slug):
        try:
            return _req('GET', '%s/addons/%s/stats' % (SUP, slug), self.token)['data']
        except Exception:
            return {}

    def logs(self, slug):
        return _req('GET', '%s/addons/%s/logs' % (SUP, slug), self.token, raw=True, timeout=20)

    def restart(self, slug):
        _req('POST', '%s/addons/%s/restart' % (SUP, slug), self.token, body={}, timeout=120)


def http_probe(url, timeout=5):
    """-> (ok, text). ANY HTTP status counts as alive (401, 404 included); a timeout or refused connection is a failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, 'HTTP %d' % r.status
    except urllib.error.HTTPError as e:
        return True, 'HTTP %d' % e.code
    except Exception as e:
        return False, type(e).__name__


class Tasks:
    """The IH Tasks add-on (token API on its own port)."""
    def __init__(self, get_opts):
        self.o = get_opts

    def _api(self, method, path, body=None):
        o = self.o()
        return _req(method, o['tasks_url'].rstrip('/') + path, o['tasks_token'], body)

    def add(self, title, desc):
        r = self._api('POST', '/tasks', {'title': title[:200], 'description': desc[:20000], 'priority': 'high', 'source': 'ih_addon_watchdog', 'allow_duplicate': True})
        t = r.get('task') or r
        return t.get('id') if isinstance(t, dict) else t

    def comment(self, n, text):
        self._api('POST', '/tasks/%d/comment' % n, {'text': text[:3800]})

    def is_open(self, n):
        t = self._api('GET', '/tasks/%d' % n)
        t = t.get('task') or t
        return t.get('stage') in ('todo', 'planning', 'approved', 'implementing', 'verifying')


def _bot_req(o, text):
    r = urllib.request.Request('https://discord.com/api/v10/channels/%s/messages' % o['discord_channel_id'], method='POST',
                               data=json.dumps({'content': text[:1900]}).encode(),
                               headers={'Authorization': 'Bot ' + o['discord_bot_token'], 'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (ih_addon_watchdog, 0.1)'})
    urllib.request.urlopen(r, timeout=15).read()


def discord_notifier(get_opts):
    def notify(text):
        o = get_opts()
        if o['discord_bot_token'] and o['discord_channel_id']:
            try:
                _bot_req(o, text)
            except Exception:
                pass
    return notify


class Media:
    """Silent-clip probe through the Home Assistant API (via the Supervisor proxy)."""
    CORE = 'http://supervisor/core/api'

    def __init__(self, get_opts, token=None, sleep=time.sleep):
        self.o, self.token, self.sleep = get_opts, token or os.environ.get('SUPERVISOR_TOKEN', ''), sleep

    def _c(self, method, path, body=None, raw=False):
        return _req(method, self.CORE + path, self.token, body, raw=raw)

    def ensure_clip(self):
        d = '/media/ih_watchdog'
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, 'silence.wav')
        if not os.path.exists(f):
            with wave.open(f, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(struct.pack('<h', 0) * 16000)    # 1 s of digital silence

    def play_probe(self, entity):
        self.ensure_clip()
        self._c('POST', '/services/media_player/play_media', {'entity_id': entity, 'media_content_id': self.o()['media_probe_clip'], 'media_content_type': 'music'})
        for _ in range(10):
            self.sleep(1)
            st = self._c('GET', '/states/' + entity).get('state')
            if st == 'playing':
                return True
        return False

    def reload_entry(self, entity):
        eid = self._c('POST', '/template', {'template': "{{ config_entry_id('%s') }}" % entity}, raw=True).strip()
        if eid and eid != 'None':
            self._c('POST', '/config/config_entries/entry/%s/reload' % eid, {})
            self.sleep(5)
