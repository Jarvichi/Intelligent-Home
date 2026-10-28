#!/usr/bin/env python3
"""Intelligent Home - Jobs - Met Office 0.1.2: child of the Intelligent Home - Jobs parent. Runs ONE job (metoffice), registers with the parent, DRY by default. All logic is in jobs_child.py (shared copy)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_child
from jobs import metoffice

SPEC = dict(job='metoffice', source='ih_jobs_metoffice', slug='ih_jobs_metoffice', title='Intelligent Home - Jobs - Met Office', version='0.1.2', port=8799, runner=metoffice.run)

if __name__ == '__main__':
    jobs_child.main(SPEC, extra_str=[('met_office_key', ''), ('timezone', 'Europe/London'), ('zone_entity', 'zone.home'), ('fallback_weather_entity', 'weather.forecast_home')], extra_bool=[])
