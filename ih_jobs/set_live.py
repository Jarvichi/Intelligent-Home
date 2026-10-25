#!/usr/bin/env python3
"""Switch the ih_jobs module between dry and live, keeping all other options (secrets are never printed).
usage: set_live.py live [job ...] | dry   (needs SUPERVISOR_TOKEN; then restart: `ha addons restart local_ih_jobs`)
live = mode live, live_jobs = the jobs named (default backupstore metoffice), old_loops_disabled true.   dry = mode dry, live_jobs [], old_loops_disabled false.
Only run live AFTER the old loops are stopped."""
import json, os, sys, urllib.request
B, H = 'http://supervisor/addons/local_ih_jobs', {'Authorization': 'Bearer ' + os.environ.get('SUPERVISOR_TOKEN', ''), 'Content-Type': 'application/json'}
JOBS = ('backupstore', 'metoffice')
def call(path, body=None):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers=H, method='POST' if body is not None else 'GET')
    return json.loads(urllib.request.urlopen(r, timeout=20).read())
if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] not in ('live', 'dry') or any(j not in JOBS for j in a[1:]):
        sys.exit(__doc__)
    live = a[0] == 'live'
    o = call('/info')['data']['options']
    o.update({'mode': 'live' if live else 'dry', 'live_jobs': (a[1:] or list(JOBS)) if live else [], 'old_loops_disabled': live})
    res = call('/options', {'options': o})
    print('options set:', res.get('result'), '| mode', o['mode'], '| live_jobs', o['live_jobs'], '| old_loops_disabled', o['old_loops_disabled'])
