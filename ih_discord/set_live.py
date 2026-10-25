#!/usr/bin/env python3
"""Switch the ih_discord module between dry and live for shopping+house, keeping all other options (tokens are never printed).
usage: set_live.py live | dry      (needs SUPERVISOR_TOKEN in the environment; then restart the add-on: `ha addons restart 32c8114c_ih_discord`)
live = dry_run false, live_features [shopping, house], old_service_disabled true.   dry = dry_run true, live_features [], old_service_disabled false."""
import json, os, sys, urllib.request
B, H = 'http://supervisor/addons/32c8114c_ih_discord', {'Authorization': 'Bearer ' + os.environ.get('SUPERVISOR_TOKEN', ''), 'Content-Type': 'application/json'}
def call(path, body=None):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers=H, method='POST' if body is not None else 'GET')
    return json.loads(urllib.request.urlopen(r, timeout=20).read())
if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('live', 'dry'):
        sys.exit(__doc__)
    live = sys.argv[1] == 'live'
    o = call('/info')['data']['options']
    o.update({'dry_run': not live, 'live_features': ['shopping', 'house'] if live else [], 'old_service_disabled': live})
    res = call('/options', {'options': o})
    print('options set:', res.get('result'), '| dry_run', o['dry_run'], '| live_features', o['live_features'], '| old_service_disabled', o['old_service_disabled'])
