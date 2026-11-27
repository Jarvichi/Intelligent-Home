#!/usr/bin/env python3
"""Intelligent Home - Jobs - Met Office 0.2.0: child of the Intelligent Home - Jobs parent. Runs ONE job (metoffice), registers with the parent, DRY by default. All shared logic is in jobs_child.py (copy)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_child
import metodb
from jobs import metoffice

VERSION = '0.2.2'
DATA = os.environ.get('IHJ_DATA', '/data')

SPEC = dict(job='metoffice', source='ih_jobs_metoffice', slug='ih_jobs_metoffice', title='Intelligent Home - Jobs - Met Office', version=VERSION, port=8799, runner=metoffice.run)

def _with_db_status():
    """Add the database health to the shared child's /status without editing the shared jobs_child.py. A failure here never breaks /status."""
    orig = jobs_child.Child.status

    def status(self):
        s = orig(self)
        try:
            h = metodb.get(DATA).health()
            s['db'] = h
            s['db_degraded'] = h['degraded']
        except Exception as e:
            s['db'] = {'degraded': True, 'error': metodb._short(e)}
            s['db_degraded'] = True
        return s
    jobs_child.Child.status = status


if __name__ == '__main__':
    store = metodb.get(DATA)           # migrations, legacy import, trash purge: before the first job run
    store.start_maintenance(jobs_child.load_options(os.environ.get('IHJ_OPTIONS', '/data/options.json'), extra_str=[('timezone', 'Europe/London')])['timezone'])
    _with_db_status()
    jobs_child.main(SPEC, extra_str=[('met_office_key', ''), ('timezone', 'Europe/London'), ('zone_entity', 'zone.home'), ('fallback_weather_entity', 'weather.forecast_home')], extra_bool=[])
