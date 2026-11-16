"""Internal authenticated API; no private values in logs."""
import datetime as dt
import hmac
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
import store
import delivery


class App:
    def __init__(self, db, token='', today=dt.date.today):
        self.db, self.token, self.today = db, token, today

    def route(self, method, path, auth='', body=None):
        if method == 'GET' and path == '/healthz':
            return 200, {'ok': True}
        if not self.token or not hmac.compare_digest(auth, 'Bearer ' + self.token):
            return 401, {'error': 'unauthorized'}
        try:
            if method == 'GET' and path == '/contracts':
                return 200, {'contracts': store.records(self.db, self.today())}
            if method == 'GET' and path == '/reminders':
                return 200, {'due': store.due(self.db, self.today())}
            if method == 'GET' and path == '/deliveries':
                return 200, {'deliveries': [{'key': k, 'recipient': r, 'status': s} for k, r, s in
                                            self.db.execute('SELECT key, recipient, status FROM deliveries')]}
            if method == 'POST' and path == '/deliveries/resolve':
                if not isinstance(body, dict) or set(body) != {'key', 'recipient', 'resolution'} or body['resolution'] not in ('sent', 'retry'):
                    return 400, {'error': 'invalid resolution'}
                with self.db:
                    if body['resolution'] == 'retry':
                        cursor = self.db.execute("DELETE FROM deliveries WHERE key=? AND recipient=? AND status='uncertain'", (body['key'], body['recipient']))
                    else:
                        cursor = self.db.execute("UPDATE deliveries SET status='sent' WHERE key=? AND recipient=? AND status='uncertain'", (body['key'], body['recipient']))
                return (200, {'ok': True}) if cursor.rowcount else (404, {'error': 'uncertain delivery not found'})
            if method == 'POST' and path == '/import':
                if not isinstance(body, dict) or set(body) != {'contracts', 'sent'}:
                    return 400, {'error': 'contracts and sent history required'}
                store.import_once(self.db, body['contracts'], body['sent'])
                return 200, {'ok': True, 'contracts': len(body['contracts']), 'sent': len(body['sent'])}
            if method == 'PUT' and path == '/contracts':
                store.save_contract(self.db, body)
                return 200, {'ok': True}
        except (ValueError, TypeError, KeyError, OverflowError):
            return 400, {'error': 'invalid data or nonempty store'}
        except Exception:
            return 500, {'error': 'store error'}
        return 404, {'error': 'not found'}


def make_server(app, port=8818):
    class Handler(BaseHTTPRequestHandler):
        def run_request(self, method):
            body = None
            if method in ('POST', 'PUT'):
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if size < 1 or size > 1048576:
                        return self.send_json(400, {'error': 'invalid body size'})
                    body = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeError):
                    return self.send_json(400, {'error': 'invalid JSON'})
            return self.send_json(*app.route(method, self.path.split('?', 1)[0], self.headers.get('Authorization', ''), body))

        def send_json(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self): self.run_request('GET')
        def do_POST(self): self.run_request('POST')
        def do_PUT(self): self.run_request('PUT')
        def log_message(self, *args): pass

    return HTTPServer(('0.0.0.0', port), Handler)


def main():
    data = os.environ.get('IHA_DATA', '/data')
    try:
        with open(os.path.join(data, 'options.json')) as f:
            opts = json.load(f)
    except (OSError, ValueError):
        opts = {}
    db = store.open_store(os.path.join(data, 'money.db'))
    if opts.get('reminders_enabled') and opts.get('assistant_url') and opts.get('assistant_token'):
        def reminders():
            while True:
                try:
                    now = dt.datetime.now()
                    if now.hour >= 8:
                        personas = dict(item.split('=', 1) for item in opts.get('recipient_personas', []) if '=' in item)
                        connection = store.open_store(os.path.join(data, 'money.db'))
                        try:
                            delivery.run(connection, now.date(), personas, lambda persona, text:
                                delivery.send(opts['assistant_url'], opts['assistant_token'], persona, text))
                        finally:
                            connection.close()
                except Exception:
                    print('reminder check failed', flush=True)
                time.sleep(3600)
        threading.Thread(target=reminders, daemon=True).start()
    if opts.get('publish_sensor') and os.environ.get('SUPERVISOR_TOKEN'):
        def publish():
            while True:
                try:
                    # Use a separate SQLite connection for this thread.
                    connection = store.open_store(os.path.join(data, 'money.db'))
                    rows = store.records(connection, dt.date.today())
                    connection.close()
                    payload = json.dumps({'state': len(rows), 'attributes': {
                        'friendly_name': 'Lifestyle money contracts', 'rows': rows,
                        'md': store.markdown(rows)}}).encode()
                    request = urllib.request.Request('http://supervisor/core/api/states/sensor.lifestyle_money_contracts',
                        data=payload, method='POST', headers={'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + os.environ['SUPERVISOR_TOKEN']})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        response.read()
                except Exception:
                    print('sensor publication failed', flush=True)
                time.sleep(3600)
        threading.Thread(target=publish, daemon=True).start()
    make_server(App(db, opts.get('api_token', ''))).serve_forever()


if __name__ == '__main__':
    main()
