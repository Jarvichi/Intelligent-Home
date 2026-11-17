#!/usr/bin/env python3
"""Validate and optionally import a private legacy contracts snapshot."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contracts', type=Path, required=True)
    parser.add_argument('--sent', type=Path, required=True)
    parser.add_argument('--url', help='add-on API base URL, required with --apply')
    parser.add_argument('--apply', action='store_true', help='import once into an empty add-on database')
    args = parser.parse_args()
    try:
        contracts = json.loads(args.contracts.read_text())
        sent = json.loads(args.sent.read_text())
        db = store.open_store(':memory:')
        store.import_once(db, contracts, sent)
        expected = store.records(db, dt.date.today())
        if not args.apply:
            print('Validation passed: %d contracts, %d sent reminders; no import performed.' % (len(contracts), len(sent)))
            return 0
        token = os.environ.get('MONEY_API_TOKEN', '')
        if not args.url or not token:
            raise ValueError('URL and MONEY_API_TOKEN required')
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        request = urllib.request.Request(args.url.rstrip('/') + '/import',
            data=json.dumps({'contracts': contracts, 'sent': sent}).encode(), headers=headers, method='POST')
        with urllib.request.urlopen(request, timeout=15) as response:
            receipt = json.load(response)
        if receipt != {'ok': True, 'contracts': len(contracts), 'sent': len(sent)}:
            raise ValueError('unexpected import receipt')
        request = urllib.request.Request(args.url.rstrip('/') + '/contracts', headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            actual = json.load(response)['contracts']
        if actual != expected:
            raise ValueError('import verification failed; inspect before retrying')
        print('Import verified: %d contracts, %d sent reminders.' % (len(contracts), len(sent)))
        return 0
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        print('Migration failed; no private data has been printed. The import may have completed; inspect the database before retrying.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
