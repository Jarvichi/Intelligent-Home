"""Admin-only Home Assistant ingress page for the clip cache: list, filter, play on a speaker, pin, delete, generate.
Only the Supervisor ingress gateway may connect. Playing on a speaker follows the add-on mode (dry = nothing is played)."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cache as cachelib

INGRESS_PEER = '172.30.32.2'
UI_ROOT = Path(__file__).parent / 'ui'
MAX_BODY = 8192
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/ui.json': ('ui.json', 'application/json'),
          '/ih-ui.css': ('ih-ui.css', 'text/css; charset=utf-8'),
          '/ih-ui.js': ('ih-ui.js', 'text/javascript; charset=utf-8'),
          '/ui.css': ('ui.css', 'text/css; charset=utf-8'),
          '/ui.js': ('ui.js', 'text/javascript; charset=utf-8')}
CLIP_RE = re.compile(r'^/api/clips/(\d{1,9})(?:/(audio|play|delete|pin))?$')


def public(row):
    """A cache row for the page: everything but the file path."""
    return {k: row[k] for k in ('id', 'engine', 'voice', 'text', 'label', 'ext', 'size', 'created', 'last_used', 'hits', 'pinned')} | {
        'auto_pinned': bool(row.get('auto_pinned')), 'uses_30d': int(row.get('uses_30d') or 0)}


def parse_range(header, size):
    """Range header -> (start, end) inclusive, None when there is no usable Range (send it all), or 'bad' (416). Single ranges only."""
    m = re.match(r'^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$', header or '')
    if not m or (not m.group(1) and not m.group(2)):
        return None
    a, b = m.group(1), m.group(2)
    if not a:   # suffix: the last N bytes
        n = int(b)
        return 'bad' if n == 0 or size == 0 else (max(0, size - n), size - 1)
    start = int(a)
    end = min(int(b), size - 1) if b else size - 1
    return 'bad' if start >= size or end < start else (start, end)


def info(spk):
    c = spk.cfg
    return {'mode': c['mode'], 'speakers': sorted(c['speakers']), 'voices': sorted(c['voices']), 'engines': c['engines'],
            'stats': spk.cache.stats() if spk.cache else None, 'can_play': bool(c['base_url'] and c['api_token'])}


def make_server(spk, port=8832, peer=INGRESS_PEER, host='0.0.0.0', ui_root=UI_ROOT):
    class Handler(BaseHTTPRequestHandler):
        def output(self, status, data, content_type='application/json', extra=()):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            for k, v in extra:
                self.send_header(k, v)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            if self.client_address[0] != peer:
                self.output(403, {'error': 'forbidden'})
                return False
            return True

        def body(self):
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ValueError('JSON required')
            n = int(self.headers.get('Content-Length', '0'))
            if not 1 <= n <= MAX_BODY:
                raise ValueError('invalid body')
            d = json.loads(self.rfile.read(n))
            if not isinstance(d, dict):
                raise ValueError('JSON object required')
            return d

        def audio(self, data, ext):
            """The clip for the page's own player, through the ingress (no signed link). Range support (206) is needed by Safari and iOS and for seeking."""
            ct, size = cachelib.MIME.get(ext, 'audio/mpeg'), len(data)
            rng = parse_range(self.headers.get('Range'), size)
            head = [('Accept-Ranges', 'bytes')]
            if rng == 'bad':
                return self.output(416, b'', ct, head + [('Content-Range', 'bytes */%d' % size)])
            if rng:
                return self.output(206, data[rng[0]:rng[1] + 1], ct, head + [('Content-Range', 'bytes %d-%d/%d' % (rng[0], rng[1], size))])
            return self.output(200, data, ct, head)

        def do_GET(self):
            if not self.allowed():
                return
            u = urlparse(self.path)
            if u.path == '/api/info':
                return self.output(200, info(spk))
            if u.path == '/api/clips':
                if not spk.cache:
                    return self.output(200, {'clips': [], 'stats': None})
                q = parse_qs(u.query, keep_blank_values=True)
                one = lambda k: (q.get(k) or [''])[0]   # noqa: E731
                v = one('voice') if 'voice' in q else '*'
                voice = None if v == '*' else spk.cfg['voices'].get(v.lower(), v)   # a voice name from the options, or '' for the engine default
                pin = {'1': True, '0': False}.get(one('pinned'))   # '' = all clips
                rows = spk.cache.list(engine=one('engine'), voice=voice, q=one('q'), pinned=pin)
                return self.output(200, {'clips': [public(r) for r in rows[:500]], 'total': len(rows), 'stats': spk.cache.stats()})
            m = CLIP_RE.match(u.path)
            if m and m.group(2) == 'audio' and spk.cache:
                row = spk.cache.by_id(m.group(1))
                if not row:
                    return self.output(404, {'error': 'not found'})
                try:
                    data = spk.cache.read(row)
                except OSError:
                    return self.output(404, {'error': 'file missing'})
                return self.audio(data, row['ext'])
            if u.path in ASSETS:
                name, ct = ASSETS[u.path]
                try:
                    return self.output(200, (ui_root / name).read_bytes(), ct)
                except OSError:
                    return self.output(404, {'error': 'not found'})
            return self.output(404, {'error': 'not found'})

        def do_POST(self):
            if not self.allowed():
                return
            path = urlparse(self.path).path
            try:
                if path in ('/api/generate', '/api/generate-play'):
                    b = self.body()
                    row, err = spk.generate(b.get('text'), b.get('voice', ''), b.get('engine', ''), b.get('label', ''), b.get('pin') is True)
                    if err:
                        return self.output(400, {'error': err})
                    out = {'ok': True, 'clip': public(row)}
                    if path == '/api/generate-play':
                        out['play'] = _play(spk, row, b.get('speaker'))
                    return self.output(200, out)
                m = CLIP_RE.match(path)
                if not m or not m.group(2) or m.group(2) == 'audio' or not spk.cache:
                    return self.output(404, {'error': 'not found'})
                row = spk.cache.by_id(m.group(1))
                if not row:
                    return self.output(404, {'error': 'not found'})
                if m.group(2) == 'delete':
                    return self.output(200, {'ok': spk.cache.delete(row['id'])})
                b = self.body()
                if m.group(2) == 'pin':
                    spk.cache.set_pinned(row['id'], b.get('pinned') is True)
                    return self.output(200, {'ok': True, 'clip': public(spk.cache.by_id(row['id']))})
                return self.output(200, _play(spk, row, b.get('speaker')))
            except (ValueError, TypeError, KeyError):
                return self.output(400, {'error': 'Check the request.'})
            except Exception:
                return self.output(500, {'error': 'That did not work.'})

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer((host, port), Handler)


def _play(spk, row, speaker):
    r = spk.play_clip(speaker, row)
    return {'status': r['status'], 'reason': r.get('reason', ''), 'dry_run_reason': r.get('dry_run_reason', '')}
