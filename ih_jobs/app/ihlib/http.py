"""Small outside-web fetcher for plugins: GET a JSON document or a picture from a configured URL, with a polite gap between calls to one host.
opener / sleep / clock are injectable for tests. Never logs URLs with secrets: callers decide what to log."""
import json, time, urllib.parse, urllib.request

DEFAULT_UA = 'ih-jobs/1.0 (home automation add-on)'
MAX_BYTES = 8 * 1024 * 1024


class Http:
    def __init__(self, opener=None, sleep=time.sleep, clock=time.time):
        self.opener, self.sleep, self.clock = opener, sleep, clock
        self._last = {}

    def get_bytes(self, url, headers=None, timeout=20, min_interval=0.0, max_bytes=MAX_BYTES):
        """Raw body of a GET (http/https only). min_interval = seconds to keep between calls to the same host."""
        u = urllib.parse.urlparse(str(url))
        if u.scheme not in ('http', 'https') or not u.hostname:
            raise ValueError('bad url')
        if min_interval:
            wait = min_interval - (self.clock() - self._last.get(u.hostname, 0.0))
            if wait > 0:
                self.sleep(wait)
            self._last[u.hostname] = self.clock()
        h = {'User-Agent': DEFAULT_UA}
        h.update(headers or {})
        with (self.opener or urllib.request.urlopen)(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('response too large')
        return data

    def get_json(self, url, headers=None, timeout=20, min_interval=0.0):
        return json.loads(self.get_bytes(url, headers, timeout, min_interval))

    def post_json(self, url, obj, headers=None, timeout=20):
        """POST a JSON document (http/https only). -> HTTP status code. The body of the answer is not returned."""
        u = urllib.parse.urlparse(str(url))
        if u.scheme not in ('http', 'https') or not u.hostname:
            raise ValueError('bad url')
        h = {'User-Agent': DEFAULT_UA, 'Content-Type': 'application/json'}
        h.update(headers or {})
        req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers=h, method='POST')
        with (self.opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            return getattr(r, 'status', 200) or 200
