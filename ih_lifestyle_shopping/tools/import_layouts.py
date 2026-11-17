#!/usr/bin/env python3
"""Validate and optionally import private legacy shopping layouts once."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import layouts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stores', required=True, type=Path)
    parser.add_argument('--aisles', required=True, type=Path)
    parser.add_argument('--url', help='internal Shopping API URL, required with --apply')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        doc = {'stores': json.loads(args.stores.read_text()), 'aisles': json.loads(args.aisles.read_text())}
        layouts.validate(doc)
        if not args.apply:
            print('Layout validation passed: %d stores; no import performed.' % len(doc['stores']['stores']))
            return 0
        token = os.environ.get('SHOPPING_API_TOKEN', '')
        if not args.url or not token:
            raise ValueError('API URL and token required')
        base=args.url.rstrip('/')
        headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'}
        with urllib.request.urlopen(urllib.request.Request(base+'/layouts',headers=headers),timeout=15) as response:
            current=json.load(response)
        if current.get('layouts') is not None:
            raise ValueError('target not empty')
        request=urllib.request.Request(base+'/layouts/import',data=json.dumps({'layouts':doc}).encode(),headers=headers,method='POST')
        with urllib.request.urlopen(request,timeout=15) as response:
            receipt=json.load(response)
        with urllib.request.urlopen(urllib.request.Request(base+'/layouts',headers=headers),timeout=15) as response:
            verified=json.load(response)
        if receipt.get('revision') != layouts.revision(doc) or verified.get('revision') != receipt['revision'] or verified.get('layouts') != doc:
            raise ValueError('verification failed')
        print('Layout import verified: %d stores.' % len(doc['stores']['stores']))
        return 0
    except (OSError,ValueError,TypeError,KeyError,urllib.error.URLError):
        print('Layout migration failed or was interrupted. Inspect the target before retrying; private data was not printed.',file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
