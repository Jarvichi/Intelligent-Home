#!/usr/bin/env python3
"""Intelligent Home - Jobs - Backup 0.1.2: child of the Intelligent Home - Jobs parent. Runs ONE job (backupstore), registers with the parent, DRY by default. All logic is in jobs_child.py (shared copy)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_child
from jobs import backupstore

SPEC = dict(job='backupstore', source='ih_jobs_backup', slug='ih_jobs_backup', title='Intelligent Home - Jobs - Backup', version='0.1.2', port=8798, runner=backupstore.run)

if __name__ == '__main__':
    jobs_child.main(SPEC, extra_str=[('drive_used_entity', ''), ('drive_total_entity', ''), ('disk_used_entity', ''), ('disk_total_entity', '')], extra_bool=[('host_info', True)])
