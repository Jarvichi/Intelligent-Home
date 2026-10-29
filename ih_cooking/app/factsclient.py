"""Tiny client for the orchestrator facts store (PUT/DELETE /facts/<module>/<name>, bearer key). stdlib only. Never logs values or the key."""
import json, urllib.error, urllib.request


class FactsClient:
    def __init__(self, url, key, module, opener=None, timeout=5):
        self.url, self.key, self.module, self.opener, self.timeout = url.rstrip('/'), key, module, opener or urllib.request.urlopen, timeout

    def _call(self, method, name, body=None):
        req = urllib.request.Request('%s/facts/%s/%s' % (self.url, self.module, name), method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        with self.opener(req, timeout=self.timeout) as r:
            return getattr(r, 'status', 200) or 200

    def put(self, name, value, ttl_s=None):
        body = {'value': value}
        if ttl_s:
            body['ttl_s'] = int(ttl_s)
        return self._call('PUT', name, body)

    def delete(self, name):
        try:
            return self._call('DELETE', name)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404   # already gone
            raise
