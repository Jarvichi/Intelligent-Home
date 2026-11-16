"""Restricted Home Assistant to-do client; never logs item text or credentials."""
import json
import urllib.request


class HA:
    def __init__(self, token, opener=urllib.request.urlopen):
        self.token, self.opener = token, opener

    def _request(self, service, data, response=False):
        if not self.token:
            raise RuntimeError('Home Assistant unavailable')
        suffix = '?return_response' if response else ''
        request = urllib.request.Request('http://supervisor/core/api/services/todo/' + service + suffix,
            data=json.dumps(data).encode(), method='POST',
            headers={'Authorization':'Bearer '+self.token, 'Content-Type':'application/json'})
        with self.opener(request, timeout=15) as result:
            return json.load(result)

    def get_items(self, entity, status):
        result = self._request('get_items', {'entity_id':entity,'status':status}, response=True)
        items = result['service_response'][entity]['items']
        if not isinstance(items, list):
            raise RuntimeError('Home Assistant list unavailable')
        return items

    def call(self, domain, service, data):
        if domain != 'todo' or service not in ('add_item', 'update_item', 'remove_item'):
            raise ValueError('Unsupported operation')
        return self._request(service, data)
