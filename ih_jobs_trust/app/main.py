#!/usr/bin/env python3
"""Intelligent Home - Jobs - AI Trust Test 0.2.0: child of the Intelligent Home - Jobs parent. Runs ONE job (trust), registers with the parent, DRY (shadow) by default. All shared logic is in jobs_child.py (copy)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_child
import trustjob, trustdb

VERSION = '0.2.0'
DATA = os.environ.get('IHJ_DATA', '/data')

SPEC = dict(job='trust', source='ih_jobs_trust', slug='ih_jobs_trust', title='Intelligent Home - Jobs - AI Trust Test', version=VERSION, port=8810, runner=trustjob.run)

def _with_db_status():
    """Add the database health to the shared child's /status without editing the shared jobs_child.py."""
    orig = jobs_child.Child.status

    def status(self):
        s = orig(self)
        h = trustdb.get(DATA).health()
        s['db'] = h
        s['db_degraded'] = h['degraded']
        return s
    jobs_child.Child.status = status


if __name__ == '__main__':
    store = trustdb.get(DATA)          # migrations, legacy import, trash purge: before the first job run
    store.start_maintenance((jobs_child.load_options(os.environ.get('IHJ_OPTIONS', '/data/options.json'), extra_str=[('timezone', 'Europe/London')])['timezone']))
    _with_db_status()
    jobs_child.main(SPEC, extra_str=[('ai_layer_url', 'http://172.30.32.1:8801'), ('ai_layer_token', ''), ('run_hour', '3'), ('todo_entity', 'todo.claude_to_do'),
                                     ('pause_s', '4'), ('providers', ''), ('exclude', 'openai'), ('timezone', 'Europe/London')], extra_bool=[('keep_answers', False)])
