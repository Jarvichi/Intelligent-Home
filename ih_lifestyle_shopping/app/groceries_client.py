"""Read spending summaries from the existing Groceries owner."""
import json
import urllib.request


class Groceries:
    def __init__(self, url, opener=urllib.request.urlopen):
        self.url, self.opener = url.rstrip('/'), opener

    def _get(self,path):
        if not self.url: raise RuntimeError('Groceries is not configured')
        with self.opener(self.url+path,timeout=15) as result:
            return json.load(result)

    def stats(self):
        return {'weeks':self._get('/stats/weeks')['weeks'],
                'due':self._get('/stats/due')['due'],
                'changes':self._get('/stats/prices')['changes']}
