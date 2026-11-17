"""Minimal authenticated Cooking client; never logs data or credentials."""
import json
import urllib.request


class Cooking:
    def __init__(self, url, token, opener=urllib.request.urlopen):
        self.url, self.token, self.opener = url.rstrip('/'), token, opener

    def _request(self, method, path, body=None):
        if not self.url or not self.token:
            raise RuntimeError('Cooking is not configured')
        raw = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=raw, method=method,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with self.opener(req,timeout=15) as response:
            return json.load(response)

    def plan(self):
        result = self._request('GET','/plan')
        return result['plan']

    def recipes(self):
        return self._request('GET','/recipe-snapshot')

    def staples(self):
        return self._request('GET','/staples')

    def merge(self, recipes, groups, have, commit):
        return self._request('POST','/shop/merge',{'recipes':recipes,'groups':groups,'have':have,'commit':commit})

    def supports_verified_ledger(self):
        try:
            result = self._request('GET','/capabilities')
            return result.get('meal_ledger_write_verified') is True
        except Exception:
            return False
