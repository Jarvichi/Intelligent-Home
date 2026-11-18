"""ih_email: token-protected HTTP API on the internal network + a small read-only status page (ingress).
GET  /healthz                      no token (the watchdog)
GET  /messages?query=&limit=&unread=1   list/search the house mailbox
GET  /messages/<id>                one message as plain text
POST /reply   {id, body, confirmed}              reply to a message (same thread, to the sender / Reply-To only)
POST /send    {to: [..], subject, body, confirmed}  a new email
Every call except /healthz needs  Authorization: Bearer <api_token>. Sending needs confirmed = true (JSON), at most max_recipients, at most max_per_day a day.
The app password and the token are never logged or returned."""
import hmac, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ihlib import db as ihdb
import mailcore
from mailcore import MailError, Mailbox

API_PORT, INGRESS_PORT = 8845, 8846
MAX_BODY_BYTES = 100000
DEFAULTS = {'gmail_address': '', 'app_password': '', 'api_token': '', 'max_recipients': 3, 'max_per_day': 5,
            'imap_host': 'imap.gmail.com', 'smtp_host': 'smtp.gmail.com', 'mailbox': 'INBOX'}


def load_options(path='/data/options.json'):
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    o = dict(DEFAULTS)
    o.update({k: v for k, v in raw.items() if k in DEFAULTS and v is not None})
    for k, lo, hi in (('max_recipients', 1, 10), ('max_per_day', 0, 50)):
        try:
            o[k] = max(lo, min(hi, int(o[k])))
        except (TypeError, ValueError):
            o[k] = DEFAULTS[k]
    return o


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()), msg, flush=True)


def token_ok(header, token):
    """Bearer token check in constant time. An empty configured token refuses everything."""
    if not token:
        return False
    m = re.match(r'^Bearer (\S+)$', header or '')
    return bool(m) and hmac.compare_digest(m.group(1).encode(), token.encode())


def make_handler(box, opts):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _fail(self, e):
            out = {'ok': False, 'error': e.code}
            if e.code == 'not_confirmed':
                out['draft'] = e.detail
                out['hint'] = 'Show this draft to the user. Call again with confirmed=true only after the user says yes.'
            self._send(e.status, out)

        def _auth(self):
            if not opts['api_token']:
                self._send(503, {'ok': False, 'error': 'api_token_not_set'})
                return False
            if not token_ok(self.headers.get('Authorization'), opts['api_token']):
                self._send(401, {'ok': False, 'error': 'unauthorized'})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/healthz':
                return self._send(200, {'ok': True})
            if not self._auth():
                return
            try:
                if u.path == '/messages':
                    q = parse_qs(u.query)
                    lim = (q.get('limit') or ['10'])[0]
                    unread = (q.get('unread') or [''])[0].lower() in ('1', 'true', 'yes')
                    return self._send(200, {'ok': True, 'messages': box.search((q.get('query') or [''])[0], int(lim) if lim.isdigit() else 10, unread)})
                m = re.fullmatch(r'/messages/(\d{1,12})', u.path)
                if m:
                    return self._send(200, {'ok': True, 'message': box.read(m.group(1))})
                return self._send(404, {'ok': False, 'error': 'not_found'})
            except MailError as e:
                return self._fail(e)
            except Exception as e:
                box.error('read', e)
                return self._send(502, {'ok': False, 'error': 'mailbox_error'})

        def do_POST(self):
            u = urlparse(self.path)
            if not self._auth():
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n > MAX_BODY_BYTES:
                    return self._send(413, {'ok': False, 'error': 'too_large'})
                obj = json.loads(self.rfile.read(n) or b'{}')
                if not isinstance(obj, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {'ok': False, 'error': 'bad_json'})
            try:
                if u.path == '/reply':
                    return self._send(200, dict(box.reply(obj.get('id'), obj.get('body'), obj.get('confirmed')), ok=True))
                if u.path == '/send':
                    return self._send(200, dict(box.send(obj.get('to'), obj.get('subject'), obj.get('body'), obj.get('confirmed')), ok=True))
                return self._send(404, {'ok': False, 'error': 'not_found'})
            except MailError as e:
                return self._fail(e)
            except Exception as e:
                box.error('post', e)
                return self._send(502, {'ok': False, 'error': 'mailbox_error'})
    return H


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IH Email</title><style>body{font:16px system-ui,sans-serif;margin:1.5rem;max-width:34rem}td{padding:.25rem 1rem .25rem 0}h1{font-size:1.3rem}</style>
<h1>IH Email</h1><table>%s</table><p>Status only. Nothing about message contents is kept here.</p>"""


def status_page(box):
    s = box.status()
    rows = [('Mailbox set up', 'yes' if s['configured'] else 'NO: set gmail_address and app_password'),
            ('Sent today', '%d of %d' % (s['sent_today'], s['max_per_day'])), ('Recipients per email, at most', s['max_recipients']),
            ('Last read', s['last_read'] or 'never'), ('Last send', s['last_send'] or 'never'), ('Last error', s['last_error'] or 'none')]
    esc = lambda v: str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return (PAGE % ''.join('<tr><td>%s</td><td>%s</td></tr>' % (esc(a), esc(b)) for a, b in rows)).encode()


def make_status_handler(box):
    class S(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = status_page(box)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return S


def main():
    opts = load_options()
    d = ihdb.open('/data/ih.db', mailcore.MIGRATIONS)
    box = Mailbox(opts, d, log=log)
    log('ih_email start configured=%s token_set=%s max_recipients=%d max_per_day=%d' % (box.configured(), bool(opts['api_token']), opts['max_recipients'], opts['max_per_day']))
    threading.Thread(target=ThreadingHTTPServer(('0.0.0.0', INGRESS_PORT), make_status_handler(box)).serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', API_PORT), make_handler(box, opts)).serve_forever()


if __name__ == '__main__':
    main()
