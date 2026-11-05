"""Small MCP client (streamable HTTP, JSON-RPC 2.0) shared by the publicapis provider and the generic `mcp` provider.
One session per client: initialize -> mcp-session-id -> notifications/initialized. Replies may be JSON or an SSE stream (the last data line wins).
If the server rejects the session (HTTP 400/404) the client re-initialises once. It never logs anything and never keeps request text."""
import json, threading, urllib.error, urllib.request
from .base import USER_AGENT

PROTOCOL = '2025-06-18'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A header with a secret must never follow a redirect to another host: refuse redirects."""
    def redirect_request(self, *a, **kw):
        return None


def no_redirect_opener():
    return urllib.request.build_opener(_NoRedirect).open


def parse_reply(raw):
    t = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
    t = t.strip()
    if t.startswith('{'):
        return json.loads(t)
    last = None
    for line in t.splitlines():
        if line.startswith('data:') and line[5:].strip():
            last = line[5:].strip()
    if last is None:
        raise ValueError('no data')
    return json.loads(last)


class McpClient:
    def __init__(self, url, opener, version, extra_headers=None, client_name='intelligent-home-ai-layer'):
        self.url, self.opener, self.version, self.client_name = url, opener, version, client_name
        self.extra_headers = dict(extra_headers or {})
        self.sid = None
        self.lock = threading.Lock()
        self.rpc_id = 0

    def _next_id(self):
        self.rpc_id += 1
        return self.rpc_id

    def post(self, payload, timeout, sid=None, want_body=True):
        h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'User-Agent': USER_AGENT}
        h.update(self.extra_headers)
        if sid:
            h['Mcp-Session-Id'] = sid
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method='POST', headers=h)
        with self.opener(req, timeout=timeout) as x:
            raw = x.read(2000000)
            hd = getattr(x, 'headers', None)
            new_sid = hd.get('mcp-session-id') if hd is not None else None
        if not want_body:
            return None, new_sid
        return parse_reply(raw), new_sid

    def init_session(self, timeout):
        body, sid = self.post({'jsonrpc': '2.0', 'id': self._next_id(), 'method': 'initialize',
                               'params': {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': self.client_name, 'version': self.version}}}, timeout)
        if not isinstance(body, dict) or 'result' not in body:
            raise ValueError('initialize failed')
        self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, timeout, sid, want_body=False)
        self.sid = sid
        return sid

    def request(self, method, params, timeout):
        """-> the JSON-RPC response dict. Re-initialised once if the server rejects the session (HTTP 400/404)."""
        with self.lock:
            for attempt in (0, 1):
                if not self.sid:
                    self.init_session(timeout)
                try:
                    body, _ = self.post({'jsonrpc': '2.0', 'id': self._next_id(), 'method': method, 'params': params}, timeout, self.sid)
                    return body
                except urllib.error.HTTPError as e:
                    if attempt == 0 and e.code in (400, 404):
                        self.sid = None
                        continue
                    raise

    def call_tool(self, name, args, timeout):
        return self.request('tools/call', {'name': name, 'arguments': args}, timeout)

    def list_tools(self, timeout, max_pages=5):
        """-> list of tool dicts from tools/list (follows nextCursor a few pages)."""
        out, cursor = [], None
        for _ in range(max_pages):
            body = self.request('tools/list', {'cursor': cursor} if cursor else {}, timeout)
            if not isinstance(body, dict) or body.get('error') or not isinstance(body.get('result'), dict):
                raise ValueError('tools/list failed')
            out += [t for t in body['result'].get('tools') or [] if isinstance(t, dict)]
            cursor = body['result'].get('nextCursor')
            if not cursor:
                break
        return out


def extract(result):
    """-> (text, is_error) from a tools/call result."""
    if not isinstance(result, dict):
        return '', True
    parts = [c.get('text', '') for c in result.get('content') or [] if isinstance(c, dict) and c.get('type', 'text') == 'text' and isinstance(c.get('text'), str)]
    txt = '\n'.join(p for p in parts if p).strip()
    if not txt and result.get('structuredContent') is not None:
        txt = json.dumps(result['structuredContent'], ensure_ascii=False)
    return txt, bool(result.get('isError'))
