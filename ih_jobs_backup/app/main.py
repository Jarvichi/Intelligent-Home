#!/usr/bin/env python3
"""Intelligent Home - Jobs - Backup 0.2.0: child of the Intelligent Home - Jobs parent. Runs ONE job (backupstore), registers with the parent, DRY by default. All logic is in jobs_child.py (shared copy).
The add-on's own SQLite database (backupdb.py) is optional for the job: if it fails, backup-storage runs carry on (degraded mode)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_child
from jobs import backupstore

VERSION = '0.2.0'
DATA = os.environ.get('IHJ_DATA', '/data')
LOCAL_TZ = 'Europe/London'      # for the nightly snapshot hour

SPEC = dict(job='backupstore', source='ih_jobs_backup', slug='ih_jobs_backup', title='Intelligent Home - Jobs - Backup', version=VERSION, port=8798, runner=backupstore.run)


def _with_db_status():
    """Add the database health to the shared child's /status without editing the shared jobs_child.py. Never breaks /status."""
    import backupdb
    orig = jobs_child.Child.status

    def status(self):
        s = orig(self)
        try:
            h = backupdb.get(DATA).health()
            s['db'], s['db_degraded'] = h, h['degraded']
        except Exception as e:
            s['db'], s['db_degraded'] = {'error': '%s: %s' % (type(e).__name__, e)}, True
        return s
    jobs_child.Child.status = status


def _start_db():
    """Migrations, import, trash purge, nightly snapshot thread. Any failure leaves the add-on running without the database."""
    try:
        import backupdb
        backupdb.get(DATA).start_maintenance(LOCAL_TZ)
        _with_db_status()
    except Exception as e:
        print('backup: database start failed, running without it: %s: %s' % (type(e).__name__, e), flush=True)


if __name__ == '__main__':
    _start_db()
    jobs_child.main(SPEC, extra_str=[('drive_used_entity', ''), ('drive_total_entity', ''), ('disk_used_entity', ''), ('disk_total_entity', '')], extra_bool=[('host_info', True)])
