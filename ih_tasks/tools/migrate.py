#!/usr/bin/env python3
"""One-off migration of the old to-do list and work queue into ih_tasks. DRY by default: it reads, maps and writes a report. Nothing else.

  python3 migrate.py --report tasks_migration_report.md                       dry run (reads the live list and queue)
  python3 migrate.py --todo-json todo.json --queue-json queue.json ...        dry run from saved files
  python3 migrate.py --live --api http://HOST:8818 --token-file FILE ...      create the tasks (needs the OWNER token: import can place a task in Approved)

Where the data comes from (run it on the Home Assistant host, in an add-on that has the tools):
  to-do items   --todo-json FILE, else the `ha-ws` helper: todo.get_items on --todo-entity (open items only)
  work queue    --queue-json FILE, else --queue-cmd "python3 /path/to/work_queue.py list --json"; with neither, the queue is left out
The live run is safe to repeat: a task that already owns the same old to-do uid or queue id is left alone.
The report holds your task titles: keep it on the house, do not publish it. The old list and the queue are never changed by this tool."""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_map as MM


def read_todo(args):
    if args.todo_json:
        with open(args.todo_json) as f:
            d = json.load(f)
    else:
        msg = {'type': 'call_service', 'domain': 'todo', 'service': 'get_items', 'target': {'entity_id': args.todo_entity},
               'service_data': {'status': ['needs_action']}, 'return_response': True}
        out = subprocess.run([args.ha_ws, json.dumps(msg)], capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise SystemExit('could not read the to-do list (%s exit %d)' % (args.ha_ws, out.returncode))
        d = json.loads(out.stdout.strip().splitlines()[-1])
    if isinstance(d, dict) and 'result' in d:
        d = (d['result'] or {}).get('response') or {}
    if isinstance(d, dict) and 'items' not in d:
        d = d.get(args.todo_entity) or next(iter(d.values()), {})
    items = d.get('items') if isinstance(d, dict) else d
    if not isinstance(items, list):
        raise SystemExit('the to-do data has no list of items')
    return [i for i in items if isinstance(i, dict) and i.get('status', 'needs_action') == 'needs_action']


def read_queue(args):
    if args.queue_json:
        with open(args.queue_json) as f:
            d = json.load(f)
    elif args.queue_cmd:
        out = subprocess.run(shlex.split(args.queue_cmd), capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise SystemExit('could not read the work queue (exit %d)' % out.returncode)
        d = json.loads(out.stdout)
    else:
        return None
    items = d.get('items') if isinstance(d, dict) else d
    if not isinstance(items, list):
        raise SystemExit('the queue data has no list of items')
    return [i for i in items if isinstance(i, dict)]


def send(api, token, task):
    req = urllib.request.Request(api.rstrip('/') + '/import', data=json.dumps({'item': task}).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {'error': json.loads(e.read()).get('error', 'HTTP %d' % e.code)}
        except ValueError:
            return {'error': 'HTTP %d' % e.code}
    except Exception as e:
        return {'error': type(e).__name__}


def main(argv=None):
    p = argparse.ArgumentParser(description='Map the old to-do list and work queue to ih_tasks (dry by default).')
    p.add_argument('--todo-json'); p.add_argument('--queue-json'); p.add_argument('--queue-cmd')
    p.add_argument('--todo-entity', default='todo.claude_to_do'); p.add_argument('--ha-ws', default='ha-ws')
    p.add_argument('--owner-name', default='', help="first name used in parked items ('NAME to ...'); only read, never written")
    p.add_argument('--report', default='tasks_migration_report.md')
    p.add_argument('--live', action='store_true'); p.add_argument('--api'); p.add_argument('--token-file')
    args = p.parse_args(argv)
    todo = read_todo(args)
    queue = read_queue(args)
    result = MM.map_all(todo, queue or [], args.owner_name)
    sent = None
    if args.live:
        if not args.api or not args.token_file:
            raise SystemExit('--live needs --api and --token-file (a file holding the owner token)')
        with open(args.token_file) as f:
            token = f.read().strip()
        sent = {'created': 0, 'existing': 0, 'failed': 0}
        for m in result['tasks'] + result['orphans']:
            r = send(args.api, token, m['task'])
            if 'error' in r:
                sent['failed'] += 1
                m['flags'].append('NOT CREATED: %s' % r['error'])
            else:
                sent['existing' if r.get('existing') else 'created'] += 1
                m['flags'].insert(0, 'task %d%s' % (r['task']['id'], ' (was already there)' if r.get('existing') else ''))
    text = MM.report(result, live=args.live, sent=sent, when=time.strftime('%Y-%m-%d %H:%M'))
    if queue is None:
        text += '\nThe work queue was not read (no --queue-json or --queue-cmd).\n'
    with open(args.report, 'w') as f:
        f.write(text)
    c = result['counts']
    print('%s: %d to-do items + %d queue jobs -> %d tasks %s; needs input %d; flagged %d. Report: %s' % (
        'LIVE' if args.live else 'DRY', c['todo_items'], c['queue_items'], c['tasks'], json.dumps(c['by_stage'], sort_keys=True), c['needs_input'], c['flagged'], args.report))
    if sent:
        print('created %d, already there %d, failed %d' % (sent['created'], sent['existing'], sent['failed']))
        return 1 if sent['failed'] else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
