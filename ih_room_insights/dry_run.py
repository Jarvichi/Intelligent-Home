#!/usr/bin/env python3
"""Dry run: prints the three outputs for the last N days from the live Home Assistant, using a temp database.
Usage: python3 dry_run.py [days=14]. Every HTTP call is checked: anything but a GET refuses and exits (proves no writes, no service calls)."""
import json, os, sys, tempfile, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
from ihlib import db as ihdb
from ihlib.ha import HA
import engine as E

calls = []


def opener(req, timeout=30):
    calls.append((req.get_method(), req.full_url.split('/api/')[-1].split('?')[0]))
    if req.get_method() != 'GET':
        sys.exit('REFUSED non-GET call: %s %s' % calls[-1])
    return urllib.request.urlopen(req, timeout=timeout)


d = ihdb.open(os.path.join(tempfile.mkdtemp(), 'ih.db'), E.MIGRATIONS)
eng = E.Engine(d, HA(opener=opener), {'history_days': int(sys.argv[1]) if len(sys.argv) > 1 else 14}, log=print)
out = eng.run_once()
for ent, r in out['rooms'].items():
    print('%-48s now %s target %s | %s min/deg from %d runs | to target: %s min | frozen: %s' % (
        ent, r['current'], r['target'], r['min_per_deg'], r['runs'], r['minutes_to_target'], r['frozen_why'] or 'no'))
print('frozen:', json.dumps(out['frozen']))
print('mould:', json.dumps(out['mould']))
print('HTTP calls: %d, methods: %s' % (len(calls), sorted({c[0] for c in calls})))
