#!/usr/bin/env python3
"""Switch the ih_discord transport between dry and live, keeping all other options (tokens are never printed).
usage: set_live.py live | dry      (needs SUPERVISOR_TOKEN in the environment; then restart the add-on)
live = dry_run false (modules in live_modules may then really post).   dry = dry_run true (every module's Discord writes are dry).
Since 0.7.0 the transport has no features, so live_features / old_service_disabled are not touched any more."""
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
    o['dry_run'] = not live
    res = call('/options', {'options': o})
    print('options set:', res.get('result'), '| dry_run', o['dry_run'], '| live_modules', o.get('live_modules'))
